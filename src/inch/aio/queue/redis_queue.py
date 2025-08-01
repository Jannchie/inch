# type: ignore
import asyncio
import json
import time
import uuid
from logging import getLogger
from typing import Any, Generic

import redis.asyncio as redis

from inch.types import Message, MessageStatus, QueueStatus, T

from .base import AsyncBaseQueue

# Lua script for atomic dequeue operation
DEQUEUE_SCRIPT = """
local function find_highest_priority_message(queue_pattern)
    local highest_priority = nil
    local best_queue = nil
    local best_message = nil

    for _, queue_key in ipairs(redis.call('KEYS', queue_pattern)) do
        local result = redis.call('ZRANGE', queue_key, 0, 0, 'WITHSCORES')
        if #result > 0 then
            local score = tonumber(result[2])
            if highest_priority == nil or score < highest_priority then
                highest_priority = score
                best_queue = queue_key
                best_message = result[1]
            end
        end
    end

    return best_queue, best_message, highest_priority
end

local queue_pattern = ARGV[1]
local processing_key = ARGV[2]
local expiration_time = ARGV[3]

local best_queue, best_message, highest_priority = find_highest_priority_message(queue_pattern)

if best_queue and best_message then
    redis.call('ZREM', best_queue, best_message)
    redis.call('HSET', processing_key, best_message, expiration_time)
    return {best_message, highest_priority}
end

return nil
"""

# Lua script for checking timeouts and re-queuing messages
TIMEOUT_CHECK_SCRIPT = """
local processing_key = ARGV[1]
local queue_prefix = ARGV[2]
local current_time = tonumber(ARGV[3])
local max_retries = tonumber(ARGV[4])
local dead_letter_key = ARGV[5]

local timed_out = {}
local processing_messages = redis.call('HGETALL', processing_key)

for i = 1, #processing_messages, 2 do
    local message_id = processing_messages[i]
    local expiration_time = tonumber(processing_messages[i + 1])

    if current_time >= expiration_time then
        table.insert(timed_out, message_id)
    end
end

for _, message_id in ipairs(timed_out) do
    redis.call('HDEL', processing_key, message_id)
    local message_key = ARGV[6] .. ':messages:' .. message_id
    local message_data = redis.call('HGETALL', message_key)

    if #message_data > 0 then
        local retry_count = tonumber(redis.call('HGET', message_key, 'retry_count') or '0') + 1

        if retry_count >= max_retries then
            redis.call('HSET', message_key, 'status', 'dead_letter')
            redis.call('LPUSH', dead_letter_key, message_id)
        else
            redis.call('HSET', message_key, 'retry_count', retry_count)
            redis.call('HSET', message_key, 'status', 'pending')

            local key = redis.call('HGET', message_key, 'key') or ''
            local priority = tonumber(redis.call('HGET', message_key, 'priority') or '0')
            local counter = tonumber(redis.call('HGET', message_key, 'counter') or '0')
            local score = -priority * 1e9 - counter

            local queue_key = queue_prefix .. ':' .. key
            redis.call('ZADD', queue_key, score, message_id)
        end
    end
end

return #timed_out
"""


class AsyncRedisQueue(AsyncBaseQueue[T], Generic[T]):
    def __init__(
        self,
        redis_client: redis.Redis | None = None,
        queue_name: str = "default",
        max_retries: int = 3,
        max_size: int | None = None,
    ) -> None:
        super().__init__(max_retries, max_size)
        self.redis = redis_client or redis.Redis(decode_responses=True)
        self.queue_name = queue_name
        self._counter = 0
        self._lock = asyncio.Lock()
        self._not_full = asyncio.Condition(self._lock)
        self.logger = getLogger("inch.queue")

        # Register Lua scripts
        self._dequeue_script = self.redis.register_script(DEQUEUE_SCRIPT)
        self._timeout_check_script = self.redis.register_script(TIMEOUT_CHECK_SCRIPT)

        # Redis key patterns
        self._pending_prefix = f"{queue_name}:pending"
        self._processing_key = f"{queue_name}:processing"
        self._dead_letter_key = f"{queue_name}:dead_letter"
        self._success_count_key = f"{queue_name}:success_count"
        self._counter_key = f"{queue_name}:counter"

    def _get_queue_key(self, key: str | None) -> str:
        """Get Redis key for a specific partition queue."""
        if key is None:
            return f"{self._pending_prefix}:"
        return f"{self._pending_prefix}:{key}"

    def _serialize_message(self, message: Message[T], counter: int | None = None) -> dict[str, Any]:
        """Serialize message to Redis hash format."""
        return {
            "data": json.dumps(message.data),
            "message_id": str(message.message_id),
            "status": message.status.value,
            "retry_count": str(message.retry_count),
            "error_message": message.error_message or "",
            "priority": str(message.priority),
            "key": message.key or "",
            "counter": str(counter or self._counter),
        }

    def _deserialize_message(self, data: dict[str, str]) -> Message[T]:
        """Deserialize message from Redis hash format."""
        return Message[T](
            data=json.loads(data["data"]),
            message_id=uuid.UUID(data["message_id"]),
            status=MessageStatus(data["status"]),
            retry_count=int(data["retry_count"]),
            error_message=data["error_message"] if data["error_message"] else None,
            priority=int(data["priority"]),
            key=data["key"] if data["key"] else None,
        )

    async def _get_and_increment_counter(self) -> int:
        """Get and increment global counter for message ordering."""
        result = await self.redis.incr(self._counter_key)
        self._counter = result
        return result

    async def enqueue(self, data: T, priority: int = 0, key: str | None = None) -> None:
        async with self._not_full:
            while await self._is_full():
                await self._not_full.wait()

            message = Message(data, priority=priority, key=key)
            message.status = MessageStatus.PENDING

            # Store message data
            message_key = f"{self.queue_name}:messages:{message.message_id}"
            counter = await self._get_and_increment_counter()
            message_data = self._serialize_message(message, counter)
            await self.redis.hset(message_key, mapping=message_data)

            # Add to appropriate queue with score = -priority * 1e9 - counter
            score = -priority * 1e9 - counter
            queue_key = self._get_queue_key(key)
            await self.redis.zadd(queue_key, {str(message.message_id): score})

    async def enqueue_batch(self, items: list[tuple[T, int]]) -> None:
        async with self._not_full:
            for data, priority in items:
                while await self._is_full():
                    await self._not_full.wait()

                message = Message(data, priority=priority, key=None)
                message.status = MessageStatus.PENDING

                # Store message data
                message_key = f"{self.queue_name}:messages:{message.message_id}"
                counter = await self._get_and_increment_counter()
                message_data = self._serialize_message(message, counter)
                await self.redis.hset(message_key, mapping=message_data)

                # Add to general queue
                score = -priority * 1e9 - counter
                queue_key = self._get_queue_key(None)
                await self.redis.zadd(queue_key, {str(message.message_id): score})

    async def enqueue_batch_with_keys(self, items: list[tuple[T, int, str | None]]) -> None:
        async with self._not_full:
            for data, priority, key in items:
                while await self._is_full():
                    await self._not_full.wait()

                message = Message(data, priority=priority, key=key)
                message.status = MessageStatus.PENDING

                # Store message data
                message_key = f"{self.queue_name}:messages:{message.message_id}"
                counter = await self._get_and_increment_counter()
                message_data = self._serialize_message(message, counter)
                await self.redis.hset(message_key, mapping=message_data)

                # Add to appropriate queue
                score = -priority * 1e9 - counter
                queue_key = self._get_queue_key(key)
                await self.redis.zadd(queue_key, {str(message.message_id): score})

    async def _is_full(self) -> bool:
        if self.max_size is None:
            return False

        # Calculate total pending messages across all queues
        pending_pattern = f"{self._pending_prefix}:*"
        total_pending = 0
        async for queue_key in self.redis.scan_iter(match=pending_pattern):
            total_pending += await self.redis.zcard(queue_key)

        current_processing = await self.redis.hlen(self._processing_key)
        current_size = total_pending + current_processing
        return current_size >= self.max_size

    async def dequeue(
        self,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> Message[T] | None:
        async with self._lock:
            # Check for timed-out messages and re-queue them
            await self._check_timeouts()

            # Determine queue pattern to search
            if key is not None:
                queue_pattern = self._get_queue_key(key)
            elif key_prefix is not None:
                queue_pattern = f"{self._pending_prefix}:{key_prefix}*"
            else:
                queue_pattern = f"{self._pending_prefix}:*"

            # Use Lua script for atomic dequeue
            expiration_time = time.time() + visibility_timeout
            result = await self._dequeue_script(args=[queue_pattern, self._processing_key, str(expiration_time)])

            if result is None:
                return None

            message_id_str, _ = result
            message_id = uuid.UUID(message_id_str)

            # Get message data
            message_key = f"{self.queue_name}:messages:{message_id}"
            message_data = await self.redis.hgetall(message_key)

            if not message_data:
                self.logger.error("Message data not found for ID %s", message_id)
                return None

            message = self._deserialize_message(message_data)
            message.status = MessageStatus.PROCESSING

            # Update message status in Redis
            await self.redis.hset(message_key, "status", MessageStatus.PROCESSING.value)

            return message

    async def dequeue_batch(
        self,
        limit: int = 10,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> list[Message[T]]:
        messages = []
        for _ in range(limit):
            message = await self.dequeue(visibility_timeout, key, key_prefix)
            if message is None:
                break
            messages.append(message)
        return messages

    async def extend_visibility(self, message_id: uuid.UUID, new_timeout: float) -> bool:
        async with self._lock:
            new_expiration = time.time() + new_timeout
            result = await self.redis.hset(self._processing_key, str(message_id), str(new_expiration))
            return result == 0  # 0 means field was updated, 1 means field was created

    async def _check_timeouts(self) -> None:
        """Check for timed-out messages and re-queue them."""
        current_time = time.time()
        await self._timeout_check_script(
            args=[
                self._processing_key,
                self._pending_prefix,
                str(current_time),
                str(self.max_retries),
                self._dead_letter_key,
                self.queue_name,
            ],
        )

    async def ack(self, message_id: uuid.UUID) -> None:
        async with self._not_full:
            message_id_str = str(message_id)

            # Remove from processing
            if await self.redis.hdel(self._processing_key, message_id_str):
                # Update message status and increment success count
                message_key = f"{self.queue_name}:messages:{message_id}"
                await self.redis.hset(message_key, "status", MessageStatus.SUCCESS.value)
                await self.redis.incr(self._success_count_key)

                # Notify waiting tasks
                self._not_full.notify()
            else:
                self.logger.warning("Message ID %s not found in processing during ack.", message_id)

    async def ack_batch(self, message_ids: list[uuid.UUID]) -> None:
        async with self._not_full:
            if not message_ids:
                return

            # Remove from processing in batch
            message_id_strs = [str(mid) for mid in message_ids]
            removed_count = await self.redis.hdel(self._processing_key, *message_id_strs)

            if removed_count > 0:
                # Update message statuses and increment success count
                pipeline = self.redis.pipeline()
                for message_id in message_ids:
                    message_key = f"{self.queue_name}:messages:{message_id}"
                    pipeline.hset(message_key, "status", MessageStatus.SUCCESS.value)
                pipeline.incrby(self._success_count_key, removed_count)
                await pipeline.execute()

                # Notify waiting tasks
                self._not_full.notify_all()

            if removed_count < len(message_ids):
                self.logger.warning("Some message IDs not found in processing during batch ack.")

    async def nack(self, message_id: uuid.UUID, error: str | None = None) -> None:
        async with self._not_full:
            message_id_str = str(message_id)

            # Remove from processing
            if not await self.redis.hdel(self._processing_key, message_id_str):
                self.logger.warning("Message ID %s not found in processing during nack.", message_id)
                return

            message_key = f"{self.queue_name}:messages:{message_id}"
            message_data = await self.redis.hgetall(message_key)

            if not message_data:
                self.logger.error("Message data not found for ID %s during nack", message_id)
                return

            retry_count = int(message_data.get("retry_count", "0")) + 1

            # Update message with error and retry count
            updates = {
                "retry_count": str(retry_count),
                "error_message": error or "",
            }

            if retry_count >= self.max_retries:
                updates["status"] = MessageStatus.DEAD_LETTER.value
                await self.redis.hset(message_key, mapping=updates)
                await self.redis.lpush(self._dead_letter_key, message_id_str)
                self._not_full.notify()
            else:
                updates["status"] = MessageStatus.PENDING.value
                await self.redis.hset(message_key, mapping=updates)

                # Re-queue message
                key = message_data.get("key", "")
                key = key if key else None
                priority = int(message_data.get("priority", "0"))
                counter = await self._get_and_increment_counter()
                score = -priority * 1e9 - counter

                queue_key = self._get_queue_key(key)
                await self.redis.zadd(queue_key, {message_id_str: score})

    async def nack_batch(self, message_ids: list[uuid.UUID], error: str | None = None) -> None:
        async with self._not_full:
            for message_id in message_ids:
                await self.nack(message_id, error)
            self._not_full.notify_all()

    async def get_status(self, key_prefix: str | None = None) -> QueueStatus:
        async with self._lock:
            await self._check_timeouts()

            if key_prefix is None:
                # Return status for all queues
                pending_pattern = f"{self._pending_prefix}:*"
                total_pending = 0
                async for queue_key in self.redis.scan_iter(match=pending_pattern):
                    total_pending += await self.redis.zcard(queue_key)

                total_processing = await self.redis.hlen(self._processing_key)
                total_success = int(await self.redis.get(self._success_count_key) or "0")
                total_dead_letter = await self.redis.llen(self._dead_letter_key)
            else:
                # Return status for queues with matching key prefix
                pending_pattern = f"{self._pending_prefix}:{key_prefix}*"
                total_pending = 0
                async for queue_key in self.redis.scan_iter(match=pending_pattern):
                    total_pending += await self.redis.zcard(queue_key)

                # Count processing messages with matching key prefix
                total_processing = 0
                processing_messages = await self.redis.hgetall(self._processing_key)
                for message_id_str in processing_messages:
                    message_key = f"{self.queue_name}:messages:{message_id_str}"
                    message_key_value = await self.redis.hget(message_key, "key")
                    if message_key_value and message_key_value.startswith(key_prefix):
                        total_processing += 1

                # For success and dead letter counts, we'd need to scan all messages
                # which is expensive, so we'll return 0 for now
                total_success = 0
                total_dead_letter = 0

            return QueueStatus(
                pending_count=total_pending,
                processing_count=total_processing,
                success_count=total_success,
                dead_letter_count=total_dead_letter,
            )

    async def get_dead_letter_messages(self) -> list[Message[T]]:
        async with self._lock:
            dead_letter_ids = await self.redis.lrange(self._dead_letter_key, 0, -1)
            messages = []

            for message_id_str in dead_letter_ids:
                message_key = f"{self.queue_name}:messages:{message_id_str}"
                message_data = await self.redis.hgetall(message_key)
                if message_data:
                    message = self._deserialize_message(message_data)
                    messages.append(message)

            return messages

    async def clear(self) -> None:
        async with self._lock:
            # Get all queue keys and message keys
            pending_keys = [queue_key async for queue_key in self.redis.scan_iter(match=f"{self._pending_prefix}:*")]

            message_keys = [message_key async for message_key in self.redis.scan_iter(match=f"{self.queue_name}:messages:*")]

            keys_to_delete = []
            keys_to_delete.extend(pending_keys)
            keys_to_delete.extend(message_keys)
            keys_to_delete.extend(
                [
                    self._processing_key,
                    self._dead_letter_key,
                    self._success_count_key,
                    self._counter_key,
                ],
            )

            if keys_to_delete:
                await self.redis.delete(*keys_to_delete)
