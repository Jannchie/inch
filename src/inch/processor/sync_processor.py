import logging
import threading
import time
import types
from collections.abc import Callable
from typing import Generic

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from inch.queue.base import SyncBaseQueue
from inch.queue.memory_queue import SyncMemoryQueue
from inch.types import T


class SyncInchPoolProcessor(Generic[T]):
    def __init__(  # noqa: PLR0913
        self,
        process_func: Callable[[T], None],
        worker: int = 4,
        queue: SyncBaseQueue[T] | None = None,
        *,
        max_queue_size: int | None = None,
        show_progress: bool = True,
        thread_name_prefix: str = "InchPoolProcessor",
    ) -> None:
        if worker <= 0:
            msg = "worker must be positive"
            raise ValueError(msg)

        self._max_workers = worker
        self._process_func = process_func
        self._queue = queue or SyncMemoryQueue[T](max_size=max_queue_size)
        self._show_progress = show_progress
        self._thread_name_prefix = thread_name_prefix

        # Thread management
        self._workers: list[threading.Thread] = []
        self._shutdown_event = threading.Event()

        # Progress tracking
        self._lock = threading.Lock()
        self._submitted_count = 0
        self._completed_count = 0
        self._failed_count = 0

        # Progress bar
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

        self.logger = logging.getLogger(__name__)

    def submit(self, data: T) -> None:
        if self._shutdown_event.is_set():
            msg = "Cannot submit new tasks after shutdown"
            raise RuntimeError(msg)

        self._queue.enqueue(data)

        with self._lock:
            self._submitted_count += 1
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                message = self._queue.dequeue(visibility_timeout=60)
                if message is None:
                    time.sleep(0.1)
                    continue

                try:
                    # Execute the process function
                    self._process_func(message.data)

                    # Acknowledge successful processing
                    self._queue.ack(message.message_id)

                    # Update progress
                    with self._lock:
                        self._completed_count += 1
                        if self._progress and self._task_id is not None:
                            self._progress.update(self._task_id, completed=self._completed_count)

                except Exception as e:
                    # Handle processing error
                    self._queue.nack(message.message_id, str(e))

                    with self._lock:
                        self._failed_count += 1

                    self.logger.exception("Task processing failed")

            except Exception:
                if not self._shutdown_event.is_set():
                    self.logger.exception("Worker thread error")
                time.sleep(0.1)

    def _start_workers(self) -> None:
        for i in range(self._max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"{self._thread_name_prefix}-{i}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def _stop_workers(self) -> None:
        self._shutdown_event.set()

        # Wait for all workers to finish
        for worker in self._workers:
            worker.join()

    def _wait_for_completion(self) -> None:
        while True:
            with self._lock:
                if self._completed_count + self._failed_count >= self._submitted_count:
                    break
            time.sleep(0.1)

    def __enter__(self) -> "SyncInchPoolProcessor[T]":
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        _ = exc_type, exc_val, exc_tb

        # Wait for all submitted tasks to complete
        self._wait_for_completion()

        # Stop workers
        self._stop_workers()

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

