import heapq
import threading
import time
from logging import getLogger
from typing import Generic

from inch.types import InFlightMessage, T

from .base import Message, MessageStatus, QueueStatus, SyncBaseQueue


class SyncMemoryQueue(SyncBaseQueue[T], Generic[T]):
    def __init__(self, max_retries: int = 3, max_size: int | None = None) -> None:
        super().__init__(max_retries, max_size)
        self._counter = 0
        self._in_flight_messages: dict[str, InFlightMessage[T]] = {}
        self._success_messages: list[Message[T]] = []
        self._dead_letter_messages: list[Message[T]] = []
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self.logger = getLogger("inch.queue")
        # Unified queue management: None key represents general queue
        self._key_queues: dict[str | None, list[tuple[int, int, Message[T]]]] = {}

    def enqueue(self, data: T, priority: int = 0, key: str | None = None) -> None:
        with self._not_full:
            # Wait until queue is not full
            while self._is_full():
                self._not_full.wait()

            message = Message(data, priority=priority, key=key)
            message.status = MessageStatus.PENDING

            # Add to appropriate queue (None key for general queue)
            if key not in self._key_queues:
                self._key_queues[key] = []
            heapq.heappush(self._key_queues[key], (-priority, self._counter, message))
            self._counter += 1

    def enqueue_batch(self, items: list[tuple[T, int]]) -> None:
        with self._not_full:
            for data, priority in items:
                # Wait until queue is not full
                while self._is_full():
                    self._not_full.wait()

                message = Message(data, priority=priority, key=None)
                message.status = MessageStatus.PENDING

                # Add to general queue (None key)
                if None not in self._key_queues:
                    self._key_queues[None] = []
                heapq.heappush(self._key_queues[None], (-priority, self._counter, message))
                self._counter += 1

    def enqueue_batch_with_keys(self, items: list[tuple[T, int, str | None]]) -> None:
        with self._not_full:
            for data, priority, key in items:
                # Wait until queue is not full
                while self._is_full():
                    self._not_full.wait()

                message = Message(data, priority=priority, key=key)
                message.status = MessageStatus.PENDING

                # Add to appropriate queue (None key for general queue)
                if key not in self._key_queues:
                    self._key_queues[key] = []
                heapq.heappush(self._key_queues[key], (-priority, self._counter, message))
                self._counter += 1

    def _is_full(self) -> bool:
        if self.max_size is None:
            return False

        # Calculate total pending messages across all queues
        total_pending = sum(len(queue) for queue in self._key_queues.values())
        current_size = total_pending + len(self._in_flight_messages)
        return current_size >= self.max_size

    def dequeue(self, visibility_timeout: int = 60, key: str | None = None, key_prefix: str | None = None) -> Message[T] | None:  # noqa: C901, PLR0912
        with self._lock:
            # 1. Check for timed-out messages and re-queue them
            self._check_timeouts()

            # 2. Determine which queues to search
            target_queues = []
            if key is not None:
                # Search specific key queue
                if self._key_queues.get(key):
                    target_queues = [(key, self._key_queues[key])]
            elif key_prefix is not None:
                # Search queues with matching prefix (exclude None key)
                for queue_key, queue in self._key_queues.items():
                    if queue and queue_key is not None and queue_key.startswith(key_prefix):
                        target_queues.append((queue_key, queue))
            else:
                # Search all queues
                for queue_key, queue in self._key_queues.items():
                    if queue:
                        target_queues.append((queue_key, queue))

            # 3. Find the highest priority message
            best_priority = float("inf")
            best_queue = None
            for _, queue in target_queues:
                if queue:
                    priority, _, _ = queue[0]
                    if priority < best_priority:  # Remember: we store -priority
                        best_priority = priority
                        best_queue = queue

            if best_queue is None:
                return None

            # 4. Pop the message from the best queue
            _, _, message = heapq.heappop(best_queue)
            message.status = MessageStatus.PROCESSING

            if message.message_id is None:
                self.logger.error("Message has None message_id, this should not happen")
                return None

            expiration_time = time.time() + visibility_timeout
            self._in_flight_messages[message.message_id] = InFlightMessage(
                message_object=message,
                expiration_time=expiration_time,
            )
            return message

    def dequeue_batch(self, limit: int = 10, visibility_timeout: int = 60, key: str | None = None, key_prefix: str | None = None) -> list[Message[T]]:
        with self._lock:
            # 1. Check for timed-out messages and re-queue them
            self._check_timeouts()

            # 2. Determine which queues to search (same logic as dequeue)
            target_queues = []
            if key is not None:
                # Search specific key queue
                if self._key_queues.get(key):
                    target_queues = [(key, self._key_queues[key])]
            elif key_prefix is not None:
                # Search queues with matching prefix (exclude None key)
                for queue_key, queue in self._key_queues.items():
                    if queue and queue_key is not None and queue_key.startswith(key_prefix):
                        target_queues.append((queue_key, queue))
            else:
                # Search all queues
                for queue_key, queue in self._key_queues.items():
                    if queue:
                        target_queues.append((queue_key, queue))

            # 3. Collect all candidates from target queues
            all_candidates = []
            all_candidates.extend([(item, queue_key) for queue_key, queue in target_queues for item in queue])

            # 4. Sort by priority and take top messages
            all_candidates.sort(key=lambda x: x[0][:2])  # Sort by (-priority, counter)
            selected_items = all_candidates[:limit]

            # 5. Remove selected messages from their queues and process them
            messages = []
            for (priority, counter, message), queue_key in selected_items:
                queue = self._key_queues[queue_key]
                if (priority, counter, message) in queue:
                    queue.remove((priority, counter, message))
                    heapq.heapify(queue)  # Restore heap property
                self._process_dequeued_message(message, visibility_timeout, messages)

            return messages

    def _process_dequeued_message(self, message: Message[T], visibility_timeout: int, messages: list[Message[T]]) -> None:
        message.status = MessageStatus.PROCESSING

        if message.message_id is None:
            self.logger.error("Message has None message_id, this should not happen")
            return

        expiration_time = time.time() + visibility_timeout
        self._in_flight_messages[message.message_id] = InFlightMessage(
            message_object=message,
            expiration_time=expiration_time,
        )
        messages.append(message)

    def extend_visibility(self, message_id: str, new_timeout: int) -> bool:
        with self._lock:
            if message_id in self._in_flight_messages:
                self._in_flight_messages[message_id].expiration_time = time.time() + new_timeout
                return True
            return False

    def _check_timeouts(self) -> None:
        """
        (Must be called within a lock)
        Checks all in-flight messages and re-queues those that have timed out.
        """
        now = time.time()
        timed_out_ids: list[str] = []

        for message_id, in_flight_msg in self._in_flight_messages.items():
            if now >= in_flight_msg.expiration_time:
                timed_out_ids.append(message_id)

        for message_id in timed_out_ids:
            in_flight_msg = self._in_flight_messages.pop(message_id)
            message = in_flight_msg.message_object

            message.retry_count += 1
            if message.retry_count >= self.max_retries:
                message.status = MessageStatus.DEAD_LETTER
                self._dead_letter_messages.append(message)
            else:
                message.status = MessageStatus.PENDING
                # Re-queue to appropriate queue (message.key could be None for general queue)
                if message.key not in self._key_queues:
                    self._key_queues[message.key] = []
                heapq.heappush(self._key_queues[message.key], (-message.priority, self._counter, message))
                self._counter += 1

    def ack(self, message: Message[T]) -> None:
        with self._not_full:
            if message.message_id is None:
                self.logger.warning("Cannot ack message with None message_id")
                return

            if message.message_id in self._in_flight_messages:
                del self._in_flight_messages[message.message_id]
                message.status = MessageStatus.SUCCESS
                self._success_messages.append(message)
                # Notify waiting threads that queue has space
                self._not_full.notify()
            else:
                self.logger.warning("Message ID %s not found in in-flight messages during ack.", message.message_id)

    def ack_batch(self, messages: list[Message[T]]) -> None:
        with self._not_full:
            for message in messages:
                if message.message_id is None:
                    self.logger.warning("Cannot ack message with None message_id")
                    continue

                if message.message_id in self._in_flight_messages:
                    del self._in_flight_messages[message.message_id]
                    message.status = MessageStatus.SUCCESS
                    self._success_messages.append(message)
                else:
                    self.logger.warning("Message ID %s not found in in-flight messages during ack.", message.message_id)
            # Notify waiting threads that queue has space
            self._not_full.notify_all()

    def nack(self, message: Message[T], error: str | None = None) -> None:
        with self._not_full:
            if message.message_id is None or message.message_id not in self._in_flight_messages:
                return

            del self._in_flight_messages[message.message_id]
            message.error_message = error
            message.retry_count += 1

            if message.retry_count >= self.max_retries:
                message.status = MessageStatus.DEAD_LETTER
                self._dead_letter_messages.append(message)
                # Notify waiting threads that queue has space
                self._not_full.notify()
            else:
                message.status = MessageStatus.PENDING
                # Re-queue to appropriate queue (message.key could be None for general queue)
                if message.key not in self._key_queues:
                    self._key_queues[message.key] = []
                heapq.heappush(self._key_queues[message.key], (-message.priority, self._counter, message))
                self._counter += 1

    def nack_batch(self, messages: list[Message[T]], error: str | None = None) -> None:
        with self._not_full:
            for message in messages:
                if message.message_id is None or message.message_id not in self._in_flight_messages:
                    continue

                del self._in_flight_messages[message.message_id]
                message.error_message = error
                message.retry_count += 1

                if message.retry_count >= self.max_retries:
                    message.status = MessageStatus.DEAD_LETTER
                    self._dead_letter_messages.append(message)
                else:
                    message.status = MessageStatus.PENDING
                    # Re-queue to appropriate queue (message.key could be None for general queue)
                    if message.key not in self._key_queues:
                        self._key_queues[message.key] = []
                    heapq.heappush(self._key_queues[message.key], (-message.priority, self._counter, message))
                    self._counter += 1
            # Notify waiting threads that queue has space
            self._not_full.notify_all()

    def get_status(self, key_prefix: str | None = None) -> QueueStatus:
        with self._lock:
            self._check_timeouts()

            if key_prefix is None:
                # Return status for all queues
                total_pending = sum(len(queue) for queue in self._key_queues.values())
                total_processing = len(self._in_flight_messages)
                total_success = len(self._success_messages)
                total_dead_letter = len(self._dead_letter_messages)
            else:
                # Return status for queues with matching key prefix
                total_pending = 0
                total_processing = 0
                total_success = 0
                total_dead_letter = 0

                # Count pending messages in matching key queues (exclude None key)
                for queue_key, queue in self._key_queues.items():
                    if queue_key is not None and queue_key.startswith(key_prefix):
                        total_pending += len(queue)

                # Count in-flight messages with matching key prefix
                for in_flight_msg in self._in_flight_messages.values():
                    if in_flight_msg.message_object.key and in_flight_msg.message_object.key.startswith(key_prefix):
                        total_processing += 1

                # Count success messages with matching key prefix
                for message in self._success_messages:
                    if message.key and message.key.startswith(key_prefix):
                        total_success += 1

                # Count dead letter messages with matching key prefix
                for message in self._dead_letter_messages:
                    if message.key and message.key.startswith(key_prefix):
                        total_dead_letter += 1

            return QueueStatus(
                pending_count=total_pending,
                processing_count=total_processing,
                success_count=total_success,
                dead_letter_count=total_dead_letter,
            )

    def get_dead_letter_messages(self) -> list[Message[T]]:
        with self._lock:
            return self._dead_letter_messages.copy()

    def clear(self) -> None:
        with self._lock:
            self._key_queues.clear()
            self._in_flight_messages.clear()
            self._success_messages.clear()
            self._dead_letter_messages.clear()
