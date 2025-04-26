import contextlib
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from queue import Empty, Queue

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

# Logger for the inch module
logger = logging.getLogger("inch")


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


class InchPoolExecutor:
    """
    A thread pool executor for Inch tasks with progress tracking.

    Manages a pool of worker threads that execute Inch tasks and tracks
    their progress in a rich progress display.
    """

    def __init__(self, name: str = "Inch", max_workers: int = 8, console: Console = None) -> None:
        """
        Initialize the executor.

        Args:
            name: Name of the executor
            max_workers: Maximum number of worker threads
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
        self.__running_tasks: dict[TaskID, Inch] = {}  # Currently running tasks
        self.__pending_tasks: Queue[Inch] = Queue()  # Tasks waiting to be executed
        self.__total_task_count = 0  # Total number of tasks submitted
        self.__completed_task_count = 0  # Number of completed tasks
        self.__overall_task_id = None  # ID of the overall progress bar
        self.__finish_event = threading.Event()  # Event to signal completion
        self.__shutdown_event = threading.Event()  # Event to signal shutdown request
        self.__lock = threading.Lock()  # Lock for thread-safety
        self.max_workers = max_workers  # Maximum number of worker threads
        self.name = name  # Name of the executor
        self.__pool = None  # Reference to the thread pool

    def __enter__(self) -> "InchPoolExecutor":
        """
        Enter the context manager.

        Initializes the progress bar and starts worker threads.

        Returns:
            Self, for use in a with statement
        """
        # Initialize the progress bar
        self.started_at = datetime.now(UTC)
        self.console.print(f":rocket: Start running [bold]{self.name}[/bold]...")
        self.__initialize_overall_progress()
        self.progress_thread = threading.Thread(target=self.__update_task_progress, daemon=True)
        self.__main_thread = threading.Thread(target=self.__run_tasks, daemon=True)
        self.__main_thread.start()
        self.progress_thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        """
        Exit the context manager.

        Waits for all tasks to complete and cleans up resources.
        """
        self.__finish_event.wait()
        self.__main_thread.join()
        self.progress_thread.join()
        self.finished_at = datetime.now(UTC)
        self.console.print(f":white_check_mark: Finished in {self.finished_at - self.started_at}")

    def submit(self, task: Inch | Callable, *args: tuple, **kwargs: dict) -> None:
        """
        Submit a task for execution.

        Args:
            task: An Inch task or a callable function
            *args: Positional arguments for the callable (if task is a callable)
            **kwargs: Keyword arguments for the callable (if task is a callable)

        Raises:
            TypeError: If task is neither an Inch nor a callable

        Note:
            If executor is shutting down, new tasks will be rejected and not added to queue.
        """
        # If shutdown has been requested, reject new tasks
        if self.__shutdown_event.is_set():
            self.console.print(f":x: Executor is shutting down, rejected task: {getattr(task, 'name', str(task))}")
            return

        self.__total_task_count += 1
        if isinstance(task, Inch):
            if overall_task := self.__progress.tasks[self.__overall_task_id]:
                overall_task.total = self.__total_task_count
            self.__pending_tasks.put(task)
        elif callable(task):
            task_instance = FuncInch(task, *args, **kwargs)
            if overall_task := self.__progress.tasks[self.__overall_task_id]:
                overall_task.total = self.__total_task_count
            self.__pending_tasks.put(task_instance)
        else:
            msg = "task must be an instance of Inch or a callable function"
            raise TypeError(msg)

    def __initialize_overall_progress(self) -> None:
        """
        Initialize the overall progress bar.
        """
        # Adding the overall progress bar
        self.__overall_task_id = self.__progress.add_task(self.name, total=self.__total_task_count)

    def run(self):
        """
        Run the executor without using a context manager.

        Equivalent to using the executor in a with statement.
        """
        self.__enter__()
        self.__exit__(None, None, None)

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """
        Gracefully shut down the executor, stopping acceptance of new tasks and optionally
        cancelling waiting tasks.

        Args:
            wait: If True, wait for all submitted tasks to complete; if False, return immediately
            cancel_pending: If True, cancel all pending tasks that haven't started executing
        """
        # Set the shutdown event to signal that no new tasks should be accepted
        self.__shutdown_event.set()

        # If cancellation of pending tasks is requested
        if cancel_pending:
            with self.__lock:
                # Clear the pending queue
                while not self.__pending_tasks.empty():
                    with contextlib.suppress(Exception):
                        self.__pending_tasks.get_nowait()

                # If no tasks are running, set the completion event
                if not self.__running_tasks:
                    self.__finish_event.set()
                    # Add a None to the queue to allow worker threads to exit
                    self.__pending_tasks.put(None)

        # If waiting for task completion is requested
        if wait:
            self.__finish_event.wait()

        self.console.print(":stop_sign: Executor shutdown initiated.")

    def __update_task_progress(self) -> None:
        """
        Update the progress bars for all running tasks.

        Runs in a separate thread to periodically update the progress display.
        """
        self.__progress.start()
        while not self.__finish_event.wait(timeout=0.05):
            # Lock to avoid thread issues when operating on running_tasks
            with self.__lock:
                for task_id, task in self.__running_tasks.items():
                    self.__progress.update(task_id, completed=task.completed, total=task.total)
        self.__progress.stop()

    def __run_tasks(self) -> None:
        """
        Main task execution loop.

        Takes tasks from the queue and submits them to the thread pool for execution.
        Sets finish_event when all tasks are completed or when shutdown is requested
        and no tasks are running.
        """

        def run_task(task: Inch) -> None:
            """
            Execute a single task and update its progress.

            Args:
                task: The Inch task to execute

            Note:
                Updates both task-specific and overall progress bars.
                Handles exceptions raised during task execution.
                Helps determine when all tasks are completed.
            """
            task_id = self.__progress.add_task(task.name, total=task.total)
            with self.__lock:
                self.__running_tasks[task_id] = task
            try:
                task()
            except Exception as e:
                self.console.print(f"Task {task.name} failed: {e}")
            self.__progress.update(task_id, completed=task.total)

            # Update the global progress bar when a task is completed
            with self.__lock:
                self.__completed_task_count += 1
                self.__progress.update(self.__overall_task_id, completed=self.__completed_task_count)

            if task_id in self.__running_tasks:
                del self.__running_tasks[task_id]
            self.__progress.remove_task(task_id)

            # Check if all tasks are completed, or if shutdown was requested and no tasks are running
            if (not self.__running_tasks and self.__pending_tasks.empty()) or (self.__shutdown_event.is_set() and not self.__running_tasks):
                self.__finish_event.set()
                # Add a None to the queue to stop the worker threads
                self.__pending_tasks.put(None)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            self.__pool = pool  # Store thread pool reference for later operations
            while not self.__finish_event.is_set():
                try:
                    task = self.__pending_tasks.get(timeout=0.1)  # Add timeout to allow checking finish_event
                    if not task:
                        break
                    pool.submit(run_task, task)
                except Exception as exc:
                    # Log exceptions, instead of silently ignoring them
                    if not isinstance(exc, Empty):  # Empty queue is normal, no need to log
                        logger.exception("Error occurred while processing task queue")
