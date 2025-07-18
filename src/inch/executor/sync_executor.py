import inspect
import logging
import threading
import time
import types
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from typing import Generic

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from inch.processor.task import Task
from inch.queue.base import SyncBaseQueue
from inch.queue.memory_queue import SyncMemoryQueue
from inch.types import R, T

logger = logging.getLogger(__name__)


class InchPoolExecutor(Generic[T, R]):
    def __init__(
        self,
        queue: SyncBaseQueue[Task[T, R]] | None = None,
        max_workers: int = 4,
        thread_name_prefix: str = "InchPoolExecutor",
        *,
        show_progress: bool = True,
    ) -> None:
        if not queue:
            queue = SyncMemoryQueue()
        self._queue = queue
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix
        self._shutdown = False
        self._workers: list[threading.Thread] = []
        self._show_progress = show_progress

        # Progress tracking
        self._lock = threading.Lock()
        self._submitted_count = 0
        self._completed_count = 0
        self._failed_count = 0

        # Progress bar
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

        self._start_workers()

    def submit(self, fn: Callable[[T], R], data: T, *args: object, **kwargs: object) -> R:
        """Submit a task and return the result directly"""
        if self._shutdown:
            msg = "Cannot schedule new futures after shutdown"
            raise RuntimeError(msg)

        future: Future = Future()
        task: Task[T, R] = Task(fn=fn, data=data, future=future, args=args, kwargs=kwargs)

        self._queue.enqueue(task)

        # Update progress
        with self._lock:
            self._submitted_count += 1
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

        # Wait for result
        return future.result()

    def map(
        self,
        fn: Callable[[T], R],
        iterable: Iterable[T],
    ) -> list[R]:
        """Process items concurrently and return results"""
        items = list(iterable)

        # Update progress with total count
        with self._lock:
            self._submitted_count += len(items)
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

        # Submit tasks
        futures = []
        for item in items:
            if self._shutdown:
                msg = "Cannot schedule new futures after shutdown"
                raise RuntimeError(msg)

            future: Future = Future()
            task: Task[T, R] = Task(fn=fn, data=item, future=future, args=(), kwargs={})
            self._queue.enqueue(task)
            futures.append(future)

        # Wait for all results
        return [future.result() for future in futures]

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        self._shutdown = True

        if cancel_futures:
            self._cancel_pending_tasks()

        if wait:
            for worker in self._workers:
                worker.join()

    def _raise_async_not_supported(self) -> None:
        msg = "Async functions are not supported in sync executor"
        raise RuntimeError(msg)

    def _cancel_pending_tasks(self) -> None:
        while True:
            message = self._queue.dequeue(visibility_timeout=1)
            if message is None:
                break
            message.data.future.cancel()
            self._queue.ack(message)

    def _start_workers(self) -> None:
        for i in range(self._max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"{self._thread_name_prefix}-{i}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self) -> None:
        while not self._shutdown:
            try:
                message = self._queue.dequeue(visibility_timeout=1)

                if message is None:
                    time.sleep(0.1)  # Small delay to prevent busy waiting
                    continue

                task = message.data

                if task.future.cancelled():
                    self._queue.ack(message)
                    continue

                # Execute task
                try:
                    if inspect.iscoroutinefunction(task.fn):
                        self._raise_async_not_supported()

                    result = task.fn(task.data, *task.args, **task.kwargs)
                    task.future.set_result(result)
                    self._queue.ack(message)

                    # Update progress
                    with self._lock:
                        self._completed_count += 1
                        if self._progress and self._task_id is not None:
                            self._progress.update(self._task_id, completed=self._completed_count)

                except Exception as e:
                    task.future.set_exception(e)
                    self._queue.nack(message, str(e))

                    # Update failed count
                    with self._lock:
                        self._failed_count += 1

                    logger.exception("Task processing failed")

            except Exception:
                logger.exception("Worker error")

    def __enter__(self) -> "InchPoolExecutor[T, R]":
        if self._show_progress:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]Processing..."),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                transient=True,
            )
            self._progress.start()
            self._task_id = self._progress.add_task("Processing", total=0)

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        _ = exc_type, exc_val, exc_tb
        self.shutdown(wait=True)

        # Stop progress bar
        if self._progress:
            self._progress.stop()

        # Log final statistics
        with self._lock:
            logger.info(
                "Processing completed: %d successful, %d failed, %d total",
                self._completed_count,
                self._failed_count,
                self._submitted_count,
            )


SyncInchPoolExecutor = InchPoolExecutor
