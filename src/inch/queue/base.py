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
    priority: int = field(default=0)
    key: str | None = field(default=None)


@dataclass
class QueueStatus:
    pending_count: int
    processing_count: int
    success_count: int
    dead_letter_count: int


class SyncBaseQueue(ABC, Generic[T]):
    def __init__(self, max_retries: int = 3, max_size: int | None = None) -> None:
        self.max_retries = max_retries
        self.max_size = max_size

    @abstractmethod
    def enqueue(self, data: T, priority: int = 0) -> None: ...

    @abstractmethod
    def enqueue_batch(self, items: list[tuple[T, int]]) -> None: ...

    @abstractmethod
    def dequeue(self, visibility_timeout: int = 60) -> Message[T] | None: ...

    @abstractmethod
    def dequeue_batch(self, limit: int = 10, visibility_timeout: int = 60) -> list[Message[T]]: ...

    @abstractmethod
    def extend_visibility(self, message_id: str, new_timeout: int) -> bool: ...

    @abstractmethod
    def ack(self, message: Message[T]) -> None: ...

    @abstractmethod
    def ack_batch(self, messages: list[Message[T]]) -> None: ...

    @abstractmethod
    def nack(self, message: Message[T], error: str | None = None) -> None: ...

    @abstractmethod
    def nack_batch(self, messages: list[Message[T]], error: str | None = None) -> None: ...

    @abstractmethod
    def get_status(self) -> QueueStatus: ...

    @abstractmethod
    def get_dead_letter_messages(self) -> list[Message[T]]: ...

    @abstractmethod
    def clear(self) -> None: ...
