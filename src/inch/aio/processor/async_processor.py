import asyncio
import logging
import threading
import types
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from inch.aio.queue.base import AsyncBaseQueue
from inch.aio.queue.memory_queue import AsyncMemoryQueue

T = TypeVar("T")


class AsyncInchPoolProcessor(Generic[T]):
    def __init__(  # noqa: PLR0913
        self,
        process_func: Callable[[T], None] | Callable[[T], Awaitable[None]],
        worker: int = 4,
        queue: AsyncBaseQueue[T] | None = None,
        *,
        max_queue_size: int | None = None,
        show_progress: bool = True,
        thread_name_prefix: str = "AsyncInchPoolProcessor",
    ) -> None:
        if worker <= 0:
            msg = "worker must be positive"
            raise ValueError(msg)

        self._max_workers = worker
        self._process_func = process_func
        self._queue = queue or AsyncMemoryQueue[T](max_size=max_queue_size)
        self._show_progress = show_progress
        self._thread_name_prefix = thread_name_prefix

        # Worker management
        self._workers: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

        # Progress tracking
        self._lock = threading.Lock()
        self._submitted_count = 0
        self._completed_count = 0
        self._failed_count = 0

        # Progress bar
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

        self.logger = logging.getLogger(__name__)

    async def submit(self, data: T) -> None:
        if self._shutdown_event.is_set():
            msg = "Cannot submit new tasks after shutdown"
            raise RuntimeError(msg)

        await self._queue.enqueue(data)

        with self._lock:
            self._submitted_count += 1
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

    async def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                message = await self._queue.dequeue(visibility_timeout=60)
                if message is None:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    # Execute the process function
                    if asyncio.iscoroutinefunction(self._process_func):
                        await self._process_func(message.data)
                    else:
                        self._process_func(message.data)

                    # Acknowledge successful processing
                    await self._queue.ack(message)

                    # Update progress
                    with self._lock:
                        self._completed_count += 1
                        if self._progress and self._task_id is not None:
                            self._progress.update(self._task_id, completed=self._completed_count)

                except Exception as e:
                    # Handle processing error
                    await self._queue.nack(message, str(e))

                    with self._lock:
                        self._failed_count += 1

                    self.logger.exception("Task processing failed")

            except Exception:
                if not self._shutdown_event.is_set():
                    self.logger.exception("Worker error")
                await asyncio.sleep(0.1)

    def _start_workers(self) -> None:
        for i in range(self._max_workers):
            worker = asyncio.create_task(
                self._worker_loop(),
                name=f"{self._thread_name_prefix}-{i}",
            )
            self._workers.append(worker)

    async def _stop_workers(self) -> None:
        self._shutdown_event.set()

        # Wait for all workers to finish
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

    async def _wait_for_completion(self) -> None:
        while True:
            with self._lock:
                if self._completed_count + self._failed_count >= self._submitted_count:
                    break
            await asyncio.sleep(0.1)

    async def __aenter__(self) -> "AsyncInchPoolProcessor[T]":
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

        self._start_workers()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        _ = exc_type, exc_val, exc_tb

        # Wait for all submitted tasks to complete
        await self._wait_for_completion()

        # Stop workers
        await self._stop_workers()

        # Stop progress bar
        if self._progress:
            self._progress.stop()

        # Log final statistics
        with self._lock:
            self.logger.info(
                "Processing completed: %d successful, %d failed, %d total",
                self._completed_count,
                self._failed_count,
                self._submitted_count,
            )

