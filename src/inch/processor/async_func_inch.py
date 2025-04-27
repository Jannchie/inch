"""
Async function-based Inch task implementation.

This module defines AsyncFuncInch, a concrete implementation of AsyncInch
that wraps async callable functions.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from inch.processor.async_inch import AsyncInch


class AsyncFuncInch(AsyncInch):
    """
    An AsyncInch task wrapper for async callable functions.

    Wraps an async function and its arguments as an AsyncInch task.
    """

    def __init__(self, func: Callable[..., Coroutine[Any, Any, Any]], *args: tuple, **kwargs: dict) -> None:
        """
        Initialize an async function-based Inch task.

        Args:
            func: The async function to execute
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
        """
        super().__init__(name=func.__name__)
        self.func = func
        self.args = args
        self.kwargs = kwargs

    async def __call__(self) -> None:
        """
        Execute the wrapped async function with the stored arguments.
        """
        await self.func(self, *self.args, **self.kwargs)
