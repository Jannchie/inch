import asyncio
import json
import uuid
from datetime import datetime, timezone
from logging import getLogger
from typing import Generic

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from inch.queue.base import Message, MessageStatus, QueueStatus
from inch.queue.sql_queue import Base, QueueMessage
from inch.types import T

from .base import AsyncBaseQueue


class AsyncSQLQueue(AsyncBaseQueue[T], Generic[T]):
    def __init__(
        self,
        connection_string: str,
        queue_name: str = "default",
        max_retries: int = 3,
        max_size: int | None = None,
    ) -> None:
        super().__init__(max_retries, max_size)
        self.queue_name = queue_name
        self.engine = create_async_engine(connection_string)
        self.session_factory = async_sessionmaker(bind=self.engine, expire_on_commit=False)
        self._counter = 0
        self._lock = asyncio.Lock()
        self._not_full = asyncio.Condition(self._lock)
        self.logger = getLogger("inch.queue")
        self._initialized = False

    async def _ensure_tables(self) -> None:
        if not self._initialized:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._initialized = True

    async def _get_counter(self) -> int:
        async with self._lock:
            self._counter += 1
            return self._counter

    def _serialize_data(self, data: T) -> str:
        return json.dumps(data)

    def _deserialize_data(self, data_str: str) -> T:
        return json.loads(data_str)

    async def enqueue(self, data: T, priority: int = 0, key: str | None = None) -> None:
        await self._ensure_tables()
        async with self._not_full:
            while await self._is_full():
                await self._not_full.wait()

            # Increment counter within the existing lock context
            self._counter += 1
            counter = self._counter

            async with self.session_factory() as session:
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
                await session.commit()

    async def enqueue_batch(self, items: list[tuple[T, int]]) -> None:
        await self._ensure_tables()
        async with self._not_full:
            for data, priority in items:
                while await self._is_full():
                    await self._not_full.wait()

                # Increment counter within the existing lock context
                self._counter += 1
                counter = self._counter

                async with self.session_factory() as session:
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
                    await session.commit()

    async def enqueue_batch_with_keys(self, items: list[tuple[T, int, str | None]]) -> None:
        await self._ensure_tables()
        async with self._not_full:
            for data, priority, key in items:
                while await self._is_full():
                    await self._not_full.wait()

                # Increment counter within the existing lock context
                self._counter += 1
                counter = self._counter

                async with self.session_factory() as session:
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
                    await session.commit()

    async def _is_full(self) -> bool:
        if self.max_size is None:
            return False

        async with self.session_factory() as session:
            pending_count = await session.scalar(
                select(func.count(QueueMessage.id)).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value),
            )

            processing_count = await session.scalar(
                select(func.count(QueueMessage.id))
                .where(QueueMessage.queue_name == self.queue_name)
                .where(QueueMessage.status == MessageStatus.PROCESSING.value),
            )

            current_size = (pending_count or 0) + (processing_count or 0)
            return current_size >= self.max_size

    async def dequeue(
        self,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> Message[T] | None:
        await self._ensure_tables()
        async with self._lock:
            await self._check_timeouts()

            async with self.session_factory() as session:
                query = select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value)

                if key is not None:
                    query = query.where(QueueMessage.key == key)
                elif key_prefix is not None:
                    query = query.where(QueueMessage.key.like(f"{key_prefix}%"))

                query = query.order_by(QueueMessage.priority.desc(), QueueMessage.counter.asc()).with_for_update(skip_locked=True)

                queue_message = await session.scalar(query)

                if queue_message is None:
                    return None

                processing_until = datetime.now(timezone.utc).timestamp() + visibility_timeout
                queue_message.status = MessageStatus.PROCESSING.value  # type: ignore
                queue_message.processing_until = datetime.fromtimestamp(processing_until, timezone.utc)  # type: ignore

                await session.commit()

                return Message[T](
                    data=self._deserialize_data(str(queue_message.data)),
                    message_id=uuid.UUID(str(queue_message.id)),
                    status=MessageStatus.PROCESSING,
                    retry_count=int(queue_message.retry_count),  # type: ignore
                    error_message=str(queue_message.error_message) if queue_message.error_message is not None else None,
                    priority=int(queue_message.priority),  # type: ignore
                    key=str(queue_message.key) if queue_message.key is not None else None,
                )

    async def dequeue_batch(
        self,
        limit: int = 10,
        visibility_timeout: float = 60,
        key: str | None = None,
        key_prefix: str | None = None,
    ) -> list[Message[T]]:
        await self._ensure_tables()
        async with self._lock:
            await self._check_timeouts()

            messages = []
            async with self.session_factory() as session:
                query = select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.PENDING.value)

                if key is not None:
                    query = query.where(QueueMessage.key == key)
                elif key_prefix is not None:
                    query = query.where(QueueMessage.key.like(f"{key_prefix}%"))

                query = query.order_by(QueueMessage.priority.desc(), QueueMessage.counter.asc()).limit(limit).with_for_update(skip_locked=True)

                result = await session.scalars(query)
                queue_messages = result.all()

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

                await session.commit()

            return messages

    async def extend_visibility(self, message_id: uuid.UUID, new_timeout: float) -> bool:
        async with self._lock, self.session_factory() as session:
            queue_message = await session.get(QueueMessage, str(message_id))
            if queue_message is None:
                return False

            if str(queue_message.status) != MessageStatus.PROCESSING.value:
                return False

            new_expiration = datetime.now(timezone.utc).timestamp() + new_timeout
            queue_message.processing_until = datetime.fromtimestamp(new_expiration, timezone.utc)  # type: ignore

            await session.commit()
            return True

    async def _check_timeouts(self) -> None:
        current_time = datetime.now(timezone.utc)

        async with self.session_factory() as session:
            result = await session.scalars(
                select(QueueMessage)
                .where(QueueMessage.queue_name == self.queue_name)
                .where(QueueMessage.status == MessageStatus.PROCESSING.value)
                .where(QueueMessage.processing_until < current_time),
            )
            timed_out_messages = result.all()

            for message in timed_out_messages:
                new_retry_count = int(message.retry_count) + 1  # type: ignore
                message.retry_count = new_retry_count  # type: ignore
                if new_retry_count >= self.max_retries:
                    message.status = MessageStatus.DEAD_LETTER.value  # type: ignore
                else:
                    message.status = MessageStatus.PENDING.value  # type: ignore

                message.processing_until = None  # type: ignore

            await session.commit()

    async def ack(self, message_id: uuid.UUID) -> None:
        async with self._not_full, self.session_factory() as session:
            queue_message = await session.get(QueueMessage, str(message_id))
            if queue_message is None:
                self.logger.warning("Message ID %s not found during ack.", message_id)
                return

            if str(queue_message.status) != MessageStatus.PROCESSING.value:
                self.logger.warning("Message ID %s is not in processing state during ack.", message_id)
                return

            queue_message.status = MessageStatus.SUCCESS.value  # type: ignore
            queue_message.processing_until = None  # type: ignore
            await session.commit()

            self._not_full.notify()

    async def ack_batch(self, message_ids: list[uuid.UUID]) -> None:
        async with self._not_full:
            if not message_ids:
                return

            async with self.session_factory() as session:
                message_id_strs = [str(mid) for mid in message_ids]
                result = await session.scalars(
                    select(QueueMessage).where(QueueMessage.id.in_(message_id_strs)),
                )
                messages = result.all()

                updated_count = 0
                for message in messages:
                    if str(message.status) == MessageStatus.PROCESSING.value:
                        message.status = MessageStatus.SUCCESS.value  # type: ignore
                        message.processing_until = None  # type: ignore
                        updated_count += 1
                    else:
                        self.logger.warning("Message ID %s is not in processing state during batch ack.", message.id)

                await session.commit()

                if updated_count < len(message_ids):
                    self.logger.warning("Some message IDs not found or not in processing state during batch ack.")

                self._not_full.notify_all()

    async def nack(self, message_id: uuid.UUID, error: str | None = None) -> None:
        async with self._not_full, self.session_factory() as session:
            queue_message = await session.get(QueueMessage, str(message_id))
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
            await session.commit()

    async def nack_batch(self, message_ids: list[uuid.UUID], error: str | None = None) -> None:
        async with self._not_full:
            for message_id in message_ids:
                await self.nack(message_id, error)
            self._not_full.notify_all()

    async def get_status(self, key_prefix: str | None = None) -> QueueStatus:
        async with self._lock:
            await self._check_timeouts()

            async with self.session_factory() as session:
                base_query = select(func.count(QueueMessage.id)).where(QueueMessage.queue_name == self.queue_name)

                if key_prefix is not None:
                    base_query = base_query.where(QueueMessage.key.like(f"{key_prefix}%"))

                pending_count = (
                    await session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.PENDING.value),
                    )
                    or 0
                )

                processing_count = (
                    await session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.PROCESSING.value),
                    )
                    or 0
                )

                success_count = (
                    await session.scalar(
                        base_query.where(QueueMessage.status == MessageStatus.SUCCESS.value),
                    )
                    or 0
                )

                dead_letter_count = (
                    await session.scalar(
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

    async def get_dead_letter_messages(self) -> list[Message[T]]:
        async with self._lock:
            messages = []
            async with self.session_factory() as session:
                result = await session.scalars(
                    select(QueueMessage).where(QueueMessage.queue_name == self.queue_name).where(QueueMessage.status == MessageStatus.DEAD_LETTER.value),
                )
                dead_letter_messages = result.all()

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

    async def clear(self) -> None:
        async with self._lock, self.session_factory() as session:
            await session.execute(
                delete(QueueMessage).where(QueueMessage.queue_name == self.queue_name),
            )
            await session.commit()
