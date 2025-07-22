import uuid
from abc import ABC, abstractmethod
from typing import Generic

# Import shared types from the main queue module
from inch.queue.base import Message, QueueStatus
from inch.types import T


class AsyncBaseQueue(ABC, Generic[T]):
    def __init__(self, max_retries: int = 3, max_size: int | None = None) -> None:
        self.max_retries = max_retries
        self.max_size = max_size

    @abstractmethod
    async def enqueue(self, data: T, priority: int = 0, key: str | None = None) -> None: ...

    @abstractmethod
    async def enqueue_batch(self, items: list[T], priority: int = 0, key: str | None = None) -> None: ...


    @abstractmethod
    async def dequeue(
        self, visibility_timeout: float = 60, key: str | None = None, key_prefix: str | None = None,
    ) -> Message[T] | None: ...

    @abstractmethod
    async def dequeue_batch(
        self, limit: int = 10, visibility_timeout: float = 60, key: str | None = None, key_prefix: str | None = None,
    ) -> list[Message[T]]: ...

    @abstractmethod
    async def extend_visibility(self, message_id: uuid.UUID, new_timeout: float) -> bool: ...

    @abstractmethod
    async def ack(self, message_id: uuid.UUID) -> None: ...

    @abstractmethod
    async def ack_batch(self, message_ids: list[uuid.UUID]) -> None: ...

    @abstractmethod
    async def nack(self, message_id: uuid.UUID, error: str | None = None) -> None: ...

    @abstractmethod
    async def nack_batch(self, message_ids: list[uuid.UUID], error: str | None = None) -> None: ...

    @abstractmethod
    async def get_status(self, key_prefix: str | None = None) -> QueueStatus: ...

    @abstractmethod
    async def get_dead_letter_messages(self) -> list[Message[T]]: ...

    @abstractmethod
    async def clear(self) -> None: ...
