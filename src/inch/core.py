"""
Core Inch class definition.

This module defines the abstract base class for all Inch tasks.
"""

from abc import ABC, abstractmethod


class Inch(ABC):
    """
    Abstract base class representing a task with progress tracking capabilities.

    Each Inch task has a name, total units of work to be done, and
    a counter for completed units of work.
    """

    def __init__(self, name: str, total: int | None = None, completed: int = 0) -> None:
        """
        Initialize an Inch task.

        Args:
            name: Name of the task
            total: Total units of work to be done, or None if unknown
            completed: Initial count of completed units
        """
        self.name = name
        self.total = total
        self.completed = completed

    @abstractmethod
    def __call__(self) -> None:
        """
        Execute the task. Must be implemented by subclasses.
        """
