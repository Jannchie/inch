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
        self._pending_queue: list[tuple[int, int, Message[T]]] = []
        self._counter = 0
        self._in_flight_messages: dict[str, InFlightMessage[T]] = {}
        self._success_messages: list[Message[T]] = []
        self._dead_letter_messages: list[Message[T]] = []
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self.logger = getLogger("inch.queue")

    def enqueue(self, data: T, priority: int = 0) -> None:
        with self._not_full:
            # Wait until queue is not full
            while self._is_full():
                self._not_full.wait()

            message = Message(data, priority=priority)
            message.status = MessageStatus.PENDING
            heapq.heappush(self._pending_queue, (-priority, self._counter, message))
            self._counter += 1

    def enqueue_batch(self, items: list[tuple[T, int]]) -> None:
        with self._not_full:
            for data, priority in items:
                # Wait until queue is not full
                while self._is_full():
                    self._not_full.wait()

                message = Message(data, priority=priority)
                message.status = MessageStatus.PENDING
                heapq.heappush(self._pending_queue, (-priority, self._counter, message))
                self._counter += 1

    def _is_full(self) -> bool:
        if self.max_size is None:
            return False
        current_size = len(self._pending_queue) + len(self._in_flight_messages)
        return current_size >= self.max_size

    def dequeue(self, visibility_timeout: int = 60) -> Message[T] | None:
        with self._lock:
            # 1. Check for timed-out messages and re-queue them
            self._check_timeouts()

            # 2. Get a new message from the pending queue
            if not self._pending_queue:
                return None

            _, _, message = heapq.heappop(self._pending_queue)
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

    def dequeue_batch(self, limit: int = 10, visibility_timeout: int = 60) -> list[Message[T]]:
        with self._lock:
            # 1. Check for timed-out messages and re-queue them
            self._check_timeouts()

            # 2. Get messages from the pending queue
            messages = []
            for _ in range(min(limit, len(self._pending_queue))):
                if not self._pending_queue:
                    break

                _, _, message = heapq.heappop(self._pending_queue)
                message.status = MessageStatus.PROCESSING

                if message.message_id is None:
                    self.logger.error("Message has None message_id, this should not happen")
                    continue

                expiration_time = time.time() + visibility_timeout
                self._in_flight_messages[message.message_id] = InFlightMessage(
                    message_object=message,
                    expiration_time=expiration_time,
                )
                messages.append(message)

            return messages

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
                heapq.heappush(self._pending_queue, (-message.priority, self._counter, message))
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
                heapq.heappush(self._pending_queue, (-message.priority, self._counter, message))
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
                    heapq.heappush(self._pending_queue, (-message.priority, self._counter, message))
                    self._counter += 1
            # Notify waiting threads that queue has space
            self._not_full.notify_all()

    def get_status(self) -> QueueStatus:
        with self._lock:
            self._check_timeouts()
            return QueueStatus(
                pending_count=len(self._pending_queue),
                processing_count=len(self._in_flight_messages),
                success_count=len(self._success_messages),
                dead_letter_count=len(self._dead_letter_messages),
            )

    def get_dead_letter_messages(self) -> list[Message[T]]:
        with self._lock:
            return self._dead_letter_messages.copy()

    def clear(self) -> None:
        with self._lock:
            self._pending_queue.clear()
            self._in_flight_messages.clear()
            self._success_messages.clear()
            self._dead_letter_messages.clear()
