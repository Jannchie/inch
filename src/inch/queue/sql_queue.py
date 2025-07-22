import json
import threading
import uuid
from datetime import datetime, timezone
from logging import getLogger
from typing import Generic

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from inch.types import T

from .base import Message, MessageStatus, QueueStatus, SyncBaseQueue


class Base(DeclarativeBase):
    pass


class QueueMessage(Base):
    __tablename__ = "queue_messages"

    id = Column(String, primary_key=True)
    queue_name = Column(String, nullable=False, index=True)
    data = Column(Text, nullable=False)
    status = Column(String, nullable=False, index=True)
    priority = Column(Integer, nullable=False, index=True)
    key = Column(String, nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    processing_until = Column(DateTime, nullable=True, index=True)
    counter = Column(Integer, nullable=False)


class SyncSQLQueue(SyncBaseQueue[T], Generic[T]):
    def __init__(
        self,
        connection_string: str,
        queue_name: str = "default",
        max_retries: int = 3,
        max_size: int | None = None,
    ) -> None:
        super().__init__(max_retries, max_size)
        self.queue_name = queue_name
        self.engine = create_engine(connection_string)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self._counter = 0
        self._lock = threading.RLock()
        self._not_full = threading.Condition(self._lock)
        self.logger = getLogger("inch.queue")

    def _get_counter(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def _serialize_data(self, data: T) -> str:
        return json.dumps(data)

    def _deserialize_data(self, data_str: str) -> T:
        return json.loads(data_str)

    def enqueue(self, data: T, priority: int = 0, key: str | None = None) -> None:
        with self._not_full:
            while self._is_full():
                self._not_full.wait()

            # Increment counter within the existing lock context
            self._counter += 1
            counter = self._counter

            with self.session_factory() as session:
                message_id = uuid.uuid4()

                queue_message = QueueMessage(
                    id=str(message_id),
                    queue_name=self.queue_name,
                    data=self._serialize_data(data),
                    status=MessageStatus.PENDING.value,
                    priority=priority,
                    key=key,
                    retry_count=0,
                    created_at=datetime.now(timezone.utc),
                    counter=counter,
                )

                session.add(queue_message)
                session.commit()

    def enqueue_batch(self, items: list[tuple[T, int]]) -> None:
        with self._not_full:
            for data, priority in items:
                while self._is_full():
                    self._not_full.wait()

                # Increment counter within the existing lock context
                self._counter += 1
                counter = self._counter

                with self.session_factory() as session:
                    message_id = uuid.uuid4()

                    queue_message = QueueMessage(
                        id=str(message_id),
                        queue_name=self.queue_name,
                        data=self._serialize_data(data),
                        status=MessageStatus.PENDING.value,
                        priority=priority,
                        key=None,
                        retry_count=0,
                        created_at=datetime.now(timezone.utc),
                        counter=counter,
                    )

                    session.add(queue_message)
                    session.commit()

    def enqueue_batch_with_keys(self, items: list[tuple[T, int, str | None]]) -> None:
        with self._not_full:
            for data, priority, key in items:
                while self._is_full():
                    self._not_full.wait()

                # Increment counter within the existing lock context
                self._counter += 1
                counter = self._counter

                with self.session_factory() as session:
                    message_id = uuid.uuid4()

                    queue_message = QueueMessage(
                        id=str(message_id),
                        queue_name=self.queue_name,
                        data=self._serialize_data(data),
                        status=MessageStatus.PENDING.value,
                        priority=priority,
                        key=key,
                        retry_count=0,
                        created_at=datetime.now(timezone.utc),
                        counter=counter,
                    )

                    session.add(queue_message)
                    session.commit()

    def _is_full(self) -> bool:
        if self.max_size is None:
            return False

        with self.session_factory() as session:
            pending_count = session.scalar(
                select(func.count(QueueMessage.id)).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value),
            )

            processing_count = session.scalar(
                select(func.count(QueueMessage.id))
                .where(QueueMessage.queue_name == self.queue_name)
                .where(QueueMessage.status == MessageStatus.PROCESSING.value),
            )

            current_size = (pending_count or 0) + (processing_count or 0)
            return current_size >= self.max_size

    def dequeue(
        self,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> Message[T] | None:
        with self._lock:
            self._check_timeouts()

            with self.session_factory() as session:
                query = select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value)

                if key is not None:
                    query = query.where(QueueMessage.key == key)
                elif key_prefix is not None:
                    query = query.where(QueueMessage.key.like(f"{key_prefix}%"))

                query = query.order_by(QueueMessage.priority.desc(), QueueMessage.counter.asc()).with_for_update(skip_locked=True)

                queue_message = session.scalar(query)

                if queue_message is None:
                    return None

                processing_until = datetime.now(timezone.utc).timestamp() + visibility_timeout
                queue_message.status = MessageStatus.PROCESSING.value  # type: ignore
                queue_message.processing_until = datetime.fromtimestamp(processing_until, timezone.utc)  # type: ignore

                session.commit()

                return Message[T](
                    data=self._deserialize_data(str(queue_message.data)),
                    message_id=uuid.UUID(str(queue_message.id)),
                    status=MessageStatus.PROCESSING,
                    retry_count=int(queue_message.retry_count),  # type: ignore
                    error_message=str(queue_message.error_message) if queue_message.error_message is not None else None,
                    priority=int(queue_message.priority),  # type: ignore
                    key=str(queue_message.key) if queue_message.key is not None else None,
                )

    def dequeue_batch(
        self,
        limit: int = 10,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> list[Message[T]]:
        with self._lock:
            self._check_timeouts()

            messages = []
            with self.session_factory() as session:
                query = select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value)

                if key is not None:
                    query = query.where(QueueMessage.key == key)
                elif key_prefix is not None:
                    query = query.where(QueueMessage.key.like(f"{key_prefix}%"))

                query = query.order_by(QueueMessage.priority.desc(), QueueMessage.counter.asc()).limit(limit).with_for_update(skip_locked=True)

                queue_messages = session.scalars(query).all()

                processing_until = datetime.now(timezone.utc).timestamp() + visibility_timeout

                for queue_message in queue_messages:
                    queue_message.status = MessageStatus.PROCESSING.value  # type: ignore
                    queue_message.processing_until = datetime.fromtimestamp(processing_until, timezone.utc)  # type: ignore

                    message = Message[T](
                        data=self._deserialize_data(str(queue_message.data)),
                        message_id=uuid.UUID(str(queue_message.id)),
                        status=MessageStatus.PROCESSING,
                        retry_count=int(queue_message.retry_count),  # type: ignore
                        error_message=str(queue_message.error_message) if queue_message.error_message is not None else None,
                        priority=int(queue_message.priority),  # type: ignore
                        key=str(queue_message.key) if queue_message.key is not None else None,
                    )
                    messages.append(message)

                session.commit()

            return messages

    def extend_visibility(self, message_id: uuid.UUID, new_timeout: float) -> bool:
        with self._lock, self.session_factory() as session:
            queue_message = session.get(QueueMessage, str(message_id))
            if queue_message is None:
                return False

            if str(queue_message.status) != MessageStatus.PROCESSING.value:
                return False

            new_expiration = datetime.now(timezone.utc).timestamp() + new_timeout
            queue_message.processing_until = datetime.fromtimestamp(new_expiration, timezone.utc)  # type: ignore

            session.commit()
            return True

    def _check_timeouts(self) -> None:
        current_time = datetime.now(timezone.utc)

        with self.session_factory() as session:
            timed_out_messages = session.scalars(
                select(QueueMessage)
                .where(QueueMessage.queue_name == self.queue_name)
                .where(QueueMessage.status == MessageStatus.PROCESSING.value)
                .where(QueueMessage.processing_until < current_time),
            ).all()

            for message in timed_out_messages:
                new_retry_count = int(message.retry_count) + 1  # type: ignore
                message.retry_count = new_retry_count  # type: ignore
                if new_retry_count >= self.max_retries:
                    message.status = MessageStatus.DEAD_LETTER.value  # type: ignore
                else:
                    message.status = MessageStatus.PENDING.value  # type: ignore

                message.processing_until = None  # type: ignore

            session.commit()

    def ack(self, message_id: uuid.UUID) -> None:
        with self._not_full, self.session_factory() as session:
            queue_message = session.get(QueueMessage, str(message_id))
            if queue_message is None:
                self.logger.warning("Message ID %s not found during ack.", message_id)
                return

            if str(queue_message.status) != MessageStatus.PROCESSING.value:
                self.logger.warning("Message ID %s is not in processing state during ack.", message_id)
                return

            queue_message.status = MessageStatus.SUCCESS.value  # type: ignore
            queue_message.processing_until = None  # type: ignore
            session.commit()

            self._not_full.notify()

    def ack_batch(self, message_ids: list[uuid.UUID]) -> None:
        with self._not_full:
            if not message_ids:
                return

            with self.session_factory() as session:
                message_id_strs = [str(mid) for mid in message_ids]
                messages = session.scalars(
                    select(QueueMessage).where(QueueMessage.id.in_(message_id_strs)),
                ).all()

                updated_count = 0
                for message in messages:
                    if str(message.status) == MessageStatus.PROCESSING.value:
                        message.status = MessageStatus.SUCCESS.value  # type: ignore
                        message.processing_until = None  # type: ignore
                        updated_count += 1
                    else:
                        self.logger.warning("Message ID %s is not in processing state during batch ack.", message.id)

                session.commit()

                if updated_count < len(message_ids):
                    self.logger.warning("Some message IDs not found or not in processing state during batch ack.")

                self._not_full.notify_all()

    def nack(self, message_id: uuid.UUID, error: str | None = None) -> None:
        with self._not_full, self.session_factory() as session:
            queue_message = session.get(QueueMessage, str(message_id))
            if queue_message is None:
                self.logger.warning("Message ID %s not found during nack.", message_id)
                return

            if str(queue_message.status) != MessageStatus.PROCESSING.value:
                self.logger.warning("Message ID %s is not in processing state during nack.", message_id)
                return

            queue_message.error_message = error  # type: ignore
            new_retry_count = int(queue_message.retry_count) + 1  # type: ignore
            queue_message.retry_count = new_retry_count  # type: ignore

            if new_retry_count >= self.max_retries:
                queue_message.status = MessageStatus.DEAD_LETTER.value  # type: ignore
                self._not_full.notify()
            else:
                queue_message.status = MessageStatus.PENDING.value  # type: ignore

            queue_message.processing_until = None  # type: ignore
            session.commit()

    def nack_batch(self, message_ids: list[uuid.UUID], error: str | None = None) -> None:
        with self._not_full:
            for message_id in message_ids:
                self.nack(message_id, error)
            self._not_full.notify_all()

    def get_status(self, key_prefix: str | None = None) -> QueueStatus:
        with self._lock:
            self._check_timeouts()

            with self.session_factory() as session:
                base_query = select(func.count(QueueMessage.id)).where(QueueMessage.queue_name == self.queue_name)

                if key_prefix is not None:
                    base_query = base_query.where(QueueMessage.key.like(f"{key_prefix}%"))

                pending_count = (
                    session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.PENDING.value),
                    )
                    or 0
                )

                processing_count = (
                    session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.PROCESSING.value),
                    )
                    or 0
                )

                success_count = (
                    session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.SUCCESS.value),
                    )
                    or 0
                )

                dead_letter_count = (
                    session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.DEAD_LETTER.value),
                    )
                    or 0
                )

                return QueueStatus(
                    pending_count=pending_count,
                    processing_count=processing_count,
                    success_count=success_count,
                    dead_letter_count=dead_letter_count,
                )

    def get_dead_letter_messages(self) -> list[Message[T]]:
        with self._lock:
            messages = []
            with self.session_factory() as session:
                dead_letter_messages = session.scalars(
                    select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.DEAD_LETTER.value),
                ).all()

                for queue_message in dead_letter_messages:
                    message = Message[T](
                        data=self._deserialize_data(str(queue_message.data)),
                        message_id=uuid.UUID(str(queue_message.id)),
                        status=MessageStatus.DEAD_LETTER,
                        retry_count=int(queue_message.retry_count),  # type: ignore
                        error_message=str(queue_message.error_message) if queue_message.error_message is not None else None,
                        priority=int(queue_message.priority),  # type: ignore
                        key=str(queue_message.key) if queue_message.key is not None else None,
                    )
                    messages.append(message)

            return messages

    def clear(self) -> None:
        with self._lock, self.session_factory() as session:
            session.query(QueueMessage).where(QueueMessage.queue_name == self.queue_name).delete()
            session.commit()
