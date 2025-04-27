"""
Function-based Inch task implementation.

This module defines FuncInch, a concrete implementation of Inch
that wraps callable functions.
"""

from collections.abc import Callable

from inch.core import Inch


class FuncInch(Inch):
    """
    An Inch task wrapper for callable functions.

    Wraps a function and its arguments as an Inch task.
    """

    def __init__(self, func: Callable, *args: tuple, **kwargs: dict) -> None:
        """
        Initialize a function-based Inch task.

        Args:
            func: The function to execute
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
        """
        super().__init__(name=func.__name__)
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def __call__(self) -> None:
        """
        Execute the wrapped function with the stored arguments.
        """
        self.func(self)
