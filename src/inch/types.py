"""Common type definitions used across the inch library."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from inch.queue.base import Message

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class InFlightMessage(Generic[T]):
    """Represents a message that is currently being processed."""

    message_object: "Message[T]"
    expiration_time: float
