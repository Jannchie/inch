from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Generic

from inch.types import R, T


@dataclass
class Task(Generic[T, R]):
    fn: Callable[[T], R] | Callable[[T], Awaitable[R]]
    data: T
    future: Future
    args: tuple[object, ...]
    kwargs: dict[str, object]

