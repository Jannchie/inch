import asyncio
import time
from collections import deque
from logging import getLogger
from typing import Any, Generic, TypeVar

from .base import BaseQueue, Message, MessageStatus, QueueStatus

T = TypeVar("T")


class MemoryQueue(BaseQueue[T], Generic[T]):
    def __init__(self, max_retries: int = 3) -> None:
        super().__init__(max_retries)
        self._pending_queue: deque[Message[T]] = deque()
        # Stores {'message_object': Message[T], 'expiration_time': float}
        self._in_flight_messages: dict[str, dict[str, Any]] = {}
        self._success_messages: list[Message[T]] = []
        self._dead_letter_messages: list[Message[T]] = []
        self._lock = asyncio.Lock()
        self.logger = getLogger("inch.queue")

    async def enqueue(self, data: T) -> None:
        async with self._lock:
            message = Message(data)
            message.status = MessageStatus.PENDING
            self._pending_queue.append(message)

    async def dequeue(self, visibility_timeout: int = 60) -> Message[T] | None:
        async with self._lock:
            # 1. Check for timed-out messages and re-queue them
            self._check_timeouts()

            # 2. Get a new message from the pending queue
            if not self._pending_queue:
                return None

            message = self._pending_queue.popleft()
            message.status = MessageStatus.PROCESSING

            if message.message_id is not None:
                expiration_time = time.time() + visibility_timeout
                self._in_flight_messages[message.message_id] = {
                    "message_object": message,
                    "expiration_time": expiration_time,
                }
            return message

    async def extend_visibility(self, message_id: str, new_timeout: int) -> bool:
        async with self._lock:
            if message_id in self._in_flight_messages:
                self._in_flight_messages[message_id]["expiration_time"] = time.time() + new_timeout
                return True
            return False

    def _check_timeouts(self) -> None:
        """
        (Must be called within a lock)
        Checks all in-flight messages and re-queues those that have timed out.
        """
        now = time.time()
        timed_out_ids: list[str] = []

        for message_id, data in self._in_flight_messages.items():
            if now >= data["expiration_time"]:
                timed_out_ids.append(message_id)

        for message_id in timed_out_ids:
            data = self._in_flight_messages.pop(message_id)
            message = data["message_object"]

            message.retry_count += 1
            if message.retry_count >= self.max_retries:
                message.status = MessageStatus.DEAD_LETTER
                self._dead_letter_messages.append(message)
            else:
                message.status = MessageStatus.PENDING
                self._pending_queue.appendleft(message)  # Re-queue to the front
            # print(f"Message {message_id} timed out and was re-queued or moved to dead letter.") # For debugging

    async def ack(self, message: Message[T]) -> None:
        async with self._lock:
            if message.message_id not in self._in_flight_messages:
                # Optionally log a warning if message_id is not found
                self.logger.warning("Message ID %s not found in in-flight messages during ack.", message.message_id)
            if message.message_id in self._in_flight_messages:
                del self._in_flight_messages[message.message_id]
                message.status = MessageStatus.SUCCESS
                self._success_messages.append(message)

    async def nack(self, message: Message[T], error: str | None = None) -> None:
        async with self._lock:
            if message.message_id is None or message.message_id not in self._in_flight_messages:
                return

            del self._in_flight_messages[message.message_id]
            message.error_message = error
            message.retry_count += 1

            if message.retry_count >= self.max_retries:
                message.status = MessageStatus.DEAD_LETTER
                self._dead_letter_messages.append(message)
            else:
                message.status = MessageStatus.PENDING
                self._pending_queue.append(message)

    async def get_status(self) -> QueueStatus:
        async with self._lock:
            self._check_timeouts()
            return QueueStatus(
                pending_count=len(self._pending_queue),
                processing_count=len(self._in_flight_messages),
                success_count=len(self._success_messages),
                dead_letter_count=len(self._dead_letter_messages),
            )

    async def get_dead_letter_messages(self) -> list[Message[T]]:
        async with self._lock:
            return self._dead_letter_messages.copy()

    async def clear(self) -> None:
        async with self._lock:
            self._pending_queue.clear()
            self._in_flight_messages.clear()
            self._success_messages.clear()
            self._dead_letter_messages.clear()
