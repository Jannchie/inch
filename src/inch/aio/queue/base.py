from abc import ABC, abstractmethod
from typing import Generic, TypeVar

# Import shared types from the main queue module
from inch.queue.base import Message, QueueStatus

T = TypeVar("T")


class AsyncBaseQueue(ABC, Generic[T]):
    def __init__(self, max_retries: int = 3, max_size: int | None = None) -> None:
        self.max_retries = max_retries
        self.max_size = max_size

    @abstractmethod
    async def enqueue(self, data: T, priority: int = 0) -> None: ...

    @abstractmethod
    async def enqueue_batch(self, items: list[tuple[T, int]]) -> None: ...

    @abstractmethod
    async def dequeue(self, visibility_timeout: int = 60) -> Message[T] | None: ...

    @abstractmethod
    async def dequeue_batch(self, limit: int = 10, visibility_timeout: int = 60) -> list[Message[T]]: ...

    @abstractmethod
    async def extend_visibility(self, message_id: str, new_timeout: int) -> bool: ...

    @abstractmethod
    async def ack(self, message: Message[T]) -> None: ...

    @abstractmethod
    async def ack_batch(self, messages: list[Message[T]]) -> None: ...

    @abstractmethod
    async def nack(self, message: Message[T], error: str | None = None) -> None: ...

    @abstractmethod
    async def nack_batch(self, messages: list[Message[T]], error: str | None = None) -> None: ...

    @abstractmethod
    async def get_status(self) -> QueueStatus: ...

    @abstractmethod
    async def get_dead_letter_messages(self) -> list[Message[T]]: ...

    @abstractmethod
    async def clear(self) -> None: ...
