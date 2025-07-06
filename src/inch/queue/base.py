import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class MessageStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    DEAD_LETTER = "dead_letter"


@dataclass
class Message(Generic[T]):
    data: T
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: MessageStatus = field(default=MessageStatus.PENDING)
    retry_count: int = field(default=0)
    error_message: str | None = field(default=None)


@dataclass
class QueueStatus:
    pending_count: int
    processing_count: int
    success_count: int
    dead_letter_count: int


class BaseQueue(ABC, Generic[T]):
    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    @abstractmethod
    async def enqueue(self, data: T) -> None: ...

    @abstractmethod
    async def dequeue(self, visibility_timeout: int = 60) -> Message[T] | None: ...

    @abstractmethod
    async def extend_visibility(self, message_id: str, new_timeout: int) -> bool: ...

    @abstractmethod
    async def ack(self, message: Message[T]) -> None: ...

    @abstractmethod
    async def nack(self, message: Message[T], error: str | None = None) -> None: ...

    @abstractmethod
    async def get_status(self) -> QueueStatus: ...

    @abstractmethod
    async def get_dead_letter_messages(self) -> list[Message[T]]: ...

    @abstractmethod
    async def clear(self) -> None: ...
