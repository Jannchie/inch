#!/usr/bin/env python3
"""
Task Manager for Project-based Task Queue

This module provides a TaskManager class that wraps the AsyncMemoryQueue
to provide project and task-type based task management with statistics.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from inch.aio.queue.memory_queue import AsyncMemoryQueue

T = TypeVar("T")


@dataclass
class TaskData:
    """Data structure for a task"""
    task_id: str
    project_id: str
    task_type: str
    data: Any
    created_at: float
    metadata: dict[str, Any] | None = None


@dataclass
class ProjectStats:
    """Statistics for a project"""
    total_count: int
    pending_count: int
    processing_count: int
    success_count: int
    failed_count: int


class TaskManager(Generic[T]):
    """
    Project-based task manager that wraps AsyncMemoryQueue

    Uses key format: "project_{project_id}:task_type_{task_type}"
    to organize tasks by project and task type.
    """

    def __init__(self, max_size: int | None = None) -> None:
        """
        Initialize the task manager

        Args:
            max_size: Maximum queue size (None for unlimited)
        """
        self.queue = AsyncMemoryQueue[TaskData](max_size=max_size)

    def _make_key(self, project_id: str, task_type: str) -> str:
        """Generate key for project and task type combination"""
        return f"project_{project_id}:task_type_{task_type}"

    def _parse_key(self, key: str) -> tuple[str, str]:
        """Parse key to extract project_id and task_type"""
        parts = key.split(":")
        if len(parts) != 2:
            msg = f"Invalid key format: {key}"
            raise ValueError(msg)

        project_part = parts[0]
        task_type_part = parts[1]

        if not project_part.startswith("project_"):
            msg = f"Invalid project part: {project_part}"
            raise ValueError(msg)
        if not task_type_part.startswith("task_type_"):
            msg = f"Invalid task type part: {task_type_part}"
            raise ValueError(msg)

        project_id = project_part[8:]  # Remove "project_" prefix
        task_type = task_type_part[10:]  # Remove "task_type_" prefix

        return project_id, task_type

    async def enqueue_task(
        self,
        task_data: TaskData,
        priority: int = 0,
    ) -> None:
        """
        Enqueue a task with specified priority

        Args:
            task_data: The task data to enqueue
            priority: Task priority (higher values = higher priority)
        """
        key = self._make_key(task_data.project_id, task_data.task_type)
        await self.queue.enqueue(task_data, priority=priority, key=key)

    async def dequeue_task(
        self,
        project_id: str | None = None,
        task_type: str | None = None,
        timeout: float = 60,
    ) -> TaskData | None:
        """
        Dequeue a task from specified project/task_type or any

        Args:
            project_id: Project ID to dequeue from (None for any)
            task_type: Task type to dequeue from (None for any)
            timeout: Timeout in seconds (None for no timeout)

        Returns:
            Task data or None if timeout
        """
        if project_id and task_type:
            key = self._make_key(project_id, task_type)
            msg = await self.queue.dequeue(key=key, visibility_timeout=timeout)
            return msg.data if msg is not None else None
        if project_id:
            key_prefix = f"project_{project_id}:"
            msg = await self.queue.dequeue(key_prefix=key_prefix, visibility_timeout=timeout)
            return msg.data if msg is not None else None
        msg = await self.queue.dequeue(visibility_timeout=timeout)
        return msg.data if msg is not None else None

    async def dequeue_batch(
        self,
        batch_size: int,
        project_id: str | None = None,
        task_type: str | None = None,
        timeout: float | None = None,  # noqa: ARG002
    ) -> list[TaskData]:
        """
        Dequeue multiple tasks as a batch

        Args:
            batch_size: Number of tasks to dequeue
            project_id: Project ID to dequeue from (None for any)
            task_type: Task type to dequeue from (None for any)
            timeout: Timeout in seconds (None for no timeout)

        Returns:
            List of task data (may be less than batch_size)
        """
        tasks = []
        for _ in range(batch_size):
            task = await self.dequeue_task(project_id, task_type, timeout=0.1)
            if task is None:
                break
            tasks.append(task)
        return tasks

    async def get_project_stats(self, project_id: str) -> ProjectStats:
        """
        Get statistics for a specific project

        Args:
            project_id: Project ID to get stats for

        Returns:
            ProjectStats object with counts
        """
        key_prefix = f"project_{project_id}:"
        status = await self.queue.get_status(key_prefix=key_prefix)

        return ProjectStats(
            total_count=status.pending_count + status.processing_count +
                       status.success_count + status.dead_letter_count,
            pending_count=status.pending_count,
            processing_count=status.processing_count,
            success_count=status.success_count,
            failed_count=status.dead_letter_count,
        )

    async def get_task_type_stats(self, project_id: str, task_type: str) -> ProjectStats:
        """
        Get statistics for a specific project and task type

        Args:
            project_id: Project ID
            task_type: Task type

        Returns:
            ProjectStats object with counts
        """
        key = self._make_key(project_id, task_type)
        status = await self.queue.get_status(key_prefix=key)

        return ProjectStats(
            total_count=status.pending_count + status.processing_count +
                       status.success_count + status.dead_letter_count,
            pending_count=status.pending_count,
            processing_count=status.processing_count,
            success_count=status.success_count,
            failed_count=status.dead_letter_count,
        )

    async def get_all_stats(self) -> ProjectStats:
        """
        Get overall statistics across all projects

        Returns:
            ProjectStats object with overall counts
        """
        status = await self.queue.get_status()

        return ProjectStats(
            total_count=status.pending_count + status.processing_count +
                       status.success_count + status.dead_letter_count,
            pending_count=status.pending_count,
            processing_count=status.processing_count,
            success_count=status.success_count,
            failed_count=status.dead_letter_count,
        )

    async def complete_task(self, message_id: uuid.UUID, *, success: bool = True) -> None:
        """
        Mark a task as completed (success or failure)

        Args:
            message_id: The message ID of the task that was processed
            success: Whether the task succeeded
        """
        if success:
            await self.queue.ack(message_id)
        else:
            await self.queue.nack(message_id)

    async def list_projects(self) -> set[str]:
        """
        Get list of all project IDs

        Returns:
            Set of project IDs
        """
        # This would require access to queue internals to list all keys
        # For now, we'll return empty set as the queue doesn't expose this
        # In a real implementation, you might maintain a separate registry
        return set()

    async def list_task_types(self, project_id: str) -> set[str]:  # noqa: ARG002
        """
        Get list of task types for a project

        Args:
            project_id: Project ID

        Returns:
            Set of task types
        """
        # Similar limitation as list_projects
        return set()
