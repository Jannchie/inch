"""
Asynchronous Inch Task Executor module.

This module provides an asynchronous pool executor for AsyncInch tasks with progress tracking.
"""

import asyncio
import logging
import signal
import types
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from rich import get_console
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from inch.processor.async_func_inch import AsyncFuncInch
from inch.processor.async_inch import AsyncInch

# Logger for the async executor module
logger = logging.getLogger("inch.processor.async_executor")


class AsyncInchPoolExecutor:
    """
    An asynchronous task executor for AsyncInch tasks with progress tracking.

    Manages a pool of asynchronous tasks and tracks their progress in a rich progress display.
    """

    def __init__(self, name: str = "AsyncInch", max_concurrency: int = 8, console: Console = None) -> None:
        """
        Initialize the asynchronous executor.

        Args:
            name: Name of the executor
            max_concurrency: Maximum number of concurrent tasks
            console: Rich console to use for output, or None to use the default
        """
        if console is None:
            console = get_console()
        self.console = console
        self.__progress: Progress = Progress(
            SpinnerColumn(style="yellow"),
            TextColumn(" {task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TaskProgressColumn(),
        )
        self.__running_tasks: dict[TaskID, AsyncInch] = {}  # Currently running tasks
        self.__pending_tasks: list[AsyncInch] = []  # Tasks waiting to be executed
        self.__total_task_count = 0  # Total number of tasks submitted
        self.__completed_task_count = 0  # Number of completed tasks
        self.__overall_task_id = None  # ID of the overall progress bar
        self.__finish_event = asyncio.Event()  # Event to signal completion
        self.__shutdown_event = asyncio.Event()  # Event to signal shutdown request
        self.max_concurrency = max_concurrency  # Maximum number of concurrent tasks
        self.name = name  # Name of the executor
        self.semaphore = asyncio.Semaphore(max_concurrency)  # Semaphore to limit concurrency
        self.__task_update_interval = 0.05  # Progress update interval in seconds
        self.__original_sigint_handler = None  # Store original SIGINT handler
        self.__background_tasks = set()  # Track background tasks to avoid garbage collection

    def __handle_keyboard_interrupt(self, _signum: int | None = None, _frame: object | None = None) -> None:
        """
        Handle KeyboardInterrupt (Ctrl+C) by initiating an immediate shutdown.

        This method is registered as a signal handler for SIGINT.
        It cancels pending tasks and triggers a graceful shutdown process.

        Args:
            _signum: Signal number (provided by signal handler)
            _frame: Current stack frame (provided by signal handler)
        """
        if not self.__shutdown_event.is_set():
            self.console.print(":stop_sign: KeyboardInterrupt detected. Initiating immediate shutdown...")
            # We need to schedule the shutdown in the event loop
            asyncio.create_task(self.shutdown(wait=False, cancel_pending=True))  # noqa: RUF006

    async def __aenter__(self) -> "AsyncInchPoolExecutor":
        """
        Enter the asynchronous context manager.

        Initializes the progress bar and starts task processing.
        Sets up KeyboardInterrupt handling.

        Returns:
            Self, for use in an async with statement
        """
        # Save the original SIGINT handler to restore it later
        self.__original_sigint_handler = signal.getsignal(signal.SIGINT)
        # Register our custom handler for KeyboardInterrupt
        signal.signal(signal.SIGINT, self.__handle_keyboard_interrupt)

        # Initialize the progress bar
        self.started_at = datetime.now(UTC)
        self.console.print(f":rocket: Start running [bold]{self.name}[/bold]...")
        self.__initialize_overall_progress()

        # Start the progress update task
        update_task = asyncio.create_task(self.__update_task_progress())
        self.__background_tasks.add(update_task)
        update_task.add_done_callback(self.__background_tasks.discard)

        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None) -> None:
        """
        Exit the asynchronous context manager.

        Waits for all tasks to complete and cleans up resources.
        Restores the original SIGINT handler.

        Args:
            exc_type: Type of exception that caused the context to be exited, if any
            exc_val: Exception instance that caused the context to be exited, if any
            exc_tb: Traceback of exception that caused the context to be exited, if any
        """
        # Record if we had a clean shutdown (all tasks completed)
        clean_finish = self.are_tasks_complete()

        # Wait for completion if no shutdown has been explicitly requested
        if not self.__shutdown_event.is_set():
            await self.shutdown(wait=True, cancel_pending=False, silent=True)

        # Restore the original SIGINT handler
        signal.signal(signal.SIGINT, self.__original_sigint_handler)

        # Only show "shut down" message if we had a forced shutdown, not natural completion
        if self.__shutdown_event.is_set() and not clean_finish:
            self.console.print(":stop_sign: Executor has been shut down.")
        else:
            self.finished_at = datetime.now(UTC)
            self.console.print(f":white_check_mark: Finished in {self.finished_at - self.started_at}")

    def submit(self, task: AsyncInch | Callable[..., Coroutine[Any, Any, Any]], *args: tuple, **kwargs: dict) -> None:
        """
        Submit a task for execution.

        Args:
            task: An AsyncInch task or an async callable function
            *args: Positional arguments for the callable (if task is a callable)
            **kwargs: Keyword arguments for the callable (if task is a callable)

        Raises:
            TypeError: If task is neither an AsyncInch nor an async callable
            RuntimeError: If executor is shutting down, new tasks will be rejected

        Note:
            If executor is shutting down, new tasks will be rejected and not added to queue.
        """
        # If shutdown has been requested, reject new tasks
        if self.__shutdown_event.is_set():
            self.console.print(f":x: Executor is shutting down, rejected task: {getattr(task, 'name', str(task))}")
            return

        self.__total_task_count += 1

        # Update the overall progress bar
        if self.__overall_task_id is not None:
            self.__progress.update(self.__overall_task_id, total=self.__total_task_count)

        # Process the task based on its type
        if isinstance(task, AsyncInch):
            self.__pending_tasks.append(task)
        elif asyncio.iscoroutinefunction(task):
            task_instance = AsyncFuncInch(task, *args, **kwargs)
            self.__pending_tasks.append(task_instance)
        else:
            msg = "task must be an instance of AsyncInch or an async callable function"
            raise TypeError(msg)

        # Start processing tasks if they're not already running
        self.__process_pending_tasks()

    async def wait(self, timeout: float | None = None) -> bool:
        """
        Wait for all submitted tasks to complete.

        This method provides a clean, public interface to wait for task completion
        without accessing internal implementation details.

        Args:
            timeout: Maximum time to wait in seconds, or None to wait indefinitely

        Returns:
            True if all tasks completed, False if timeout occurred before completion
        """
        if self.__total_task_count == 0:
            return True

        if self.__completed_task_count >= self.__total_task_count:
            return True

        # Wait for the finish event which is set when all tasks complete
        try:
            if timeout is not None:
                return await asyncio.wait_for(self.__finish_event.wait(), timeout)
            await self.__finish_event.wait()
        except asyncio.TimeoutError:
            return False
        else:
            return True

    def are_tasks_complete(self) -> bool:
        """
        Check if all submitted tasks have completed.

        Returns:
            True if all tasks have completed, False otherwise
        """
        return self.__total_task_count > 0 and self.__completed_task_count >= self.__total_task_count

    def __initialize_overall_progress(self) -> None:
        """
        Initialize the overall progress bar.
        """
        # Adding the overall progress bar
        self.__overall_task_id = self.__progress.add_task(self.name, total=self.__total_task_count)

    def __process_pending_tasks(self) -> None:
        """
        Start processing pending tasks if there are available slots.

        This method creates asyncio tasks for any pending tasks up to the concurrency limit.
        """
        # Check how many tasks are already running
        running_count = len(self.__running_tasks)

        # How many more tasks we can start
        available_slots = self.max_concurrency - running_count

        # Start as many pending tasks as we can
        tasks_to_start = min(available_slots, len(self.__pending_tasks))

        for _ in range(tasks_to_start):
            if not self.__pending_tasks:
                break

            task = self.__pending_tasks.pop(0)
            asyncio_task = asyncio.create_task(self.__run_task(task))
            self.__background_tasks.add(asyncio_task)
            asyncio_task.add_done_callback(self.__background_tasks.discard)

    async def __run_task(self, task: AsyncInch) -> None:
        """
        Execute a single AsyncInch task and update its progress.

        Args:
            task: The AsyncInch task to execute
        """
        # Add a progress bar for this task
        task_id = self.__progress.add_task(task.name, total=task.total)
        self.__running_tasks[task_id] = task

        # Acquire semaphore to limit concurrency
        async with self.semaphore:
            try:
                await task()
            except Exception as e:
                self.console.print(f"Task {task.name} failed: {e}")
                logger.exception("Error occurred while processing task %s", task.name)
            finally:
                # Update the progress bar to completion
                if task.total is not None:
                    self.__progress.update(task_id, completed=task.total)

                # Update the global progress bar when a task is completed
                self.__completed_task_count += 1
                self.__progress.update(self.__overall_task_id, completed=self.__completed_task_count)

                # Remove the task from running tasks
                if task_id in self.__running_tasks:
                    del self.__running_tasks[task_id]
                self.__progress.remove_task(task_id)

                # Check if all tasks are complete
                if self.__completed_task_count >= self.__total_task_count:
                    self.__finish_event.set()

                # Start processing more pending tasks if available
                self.__process_pending_tasks()

    async def __update_task_progress(self) -> None:
        """
        Update the progress bars for all running tasks.

        Runs in a separate task to periodically update the progress display.
        """
        self.__progress.start()
        try:
            while not self.__finish_event.is_set() and not self.__shutdown_event.is_set():
                # Update progress for all running tasks
                for task_id, task in self.__running_tasks.items():
                    self.__progress.update(task_id, completed=task.completed, total=task.total)

                # Wait a bit before updating again
                await asyncio.sleep(self.__task_update_interval)
        finally:
            self.__progress.stop()

    async def run(self) -> None:
        """
        Run the executor without using a context manager.

        Equivalent to using the executor in an async with statement.
        Sets up KeyboardInterrupt handling and ensures it's cleaned up.
        """
        try:
            async with self:
                # Wait for all tasks to complete or for shutdown
                await self.__finish_event.wait()
        except KeyboardInterrupt:
            # Handle KeyboardInterrupt if not caught elsewhere
            await self.shutdown(wait=False, cancel_pending=True)

    async def shutdown(self, *, wait: bool = True, cancel_pending: bool = False, silent: bool = False) -> None:
        """
        Gracefully shut down the executor, stopping acceptance of new tasks and optionally
        cancelling waiting tasks.

        Args:
            wait: If True, wait for all submitted tasks to complete; if False, return immediately
            cancel_pending: If True, cancel all pending tasks that haven't started executing
            silent: If True, suppresses logging messages during shutdown
        """
        # Signal that no new tasks should be accepted
        self.__shutdown_event.set()

        # If cancellation of pending tasks is requested
        if cancel_pending:
            # Clear the pending queue
            self.__pending_tasks.clear()

            # If no tasks are running, set the completion event
            if not self.__running_tasks:
                self.__finish_event.set()

        # If waiting for task completion is requested
        if wait and not self.__finish_event.is_set():
            # Wait for all tasks to complete
            remaining_tasks = len(self.__running_tasks) + len(self.__pending_tasks)
            if remaining_tasks > 0:
                if not silent:
                    self.console.print(f"Waiting for {remaining_tasks} tasks to complete...")
                await self.__finish_event.wait()
