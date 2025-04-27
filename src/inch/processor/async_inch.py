"""
Asynchronous Inch class definition.

This module defines the abstract base class for all asynchronous Inch tasks.
"""

from abc import abstractmethod

from inch.core import Inch


class AsyncInch(Inch):
    """
    Abstract base class representing an asynchronous task with progress tracking capabilities.

    Each AsyncInch task has a name, total units of work to be done, and
    a counter for completed units of work.
    """

    @abstractmethod
    async def __call__(self) -> None:
        """
        Execute the task asynchronously. Must be implemented by subclasses.
        """
