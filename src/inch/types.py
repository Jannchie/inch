"""Common type definitions used across the inch library."""

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class MessageStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    DEAD_LETTER = "dead_letter"


@dataclass
class Message(Generic[T]):
    data: T
    message_id: uuid.UUID = field(default_factory=uuid.uuid4)
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


@dataclass
class InFlightMessage(Generic[T]):
    """Represents a message that is currently being processed."""

    message_object: Message[T]
    expiration_time: float
