import asyncio
import contextlib
import inspect
import logging
import threading
import types
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import Future
from typing import Generic, TypeVar

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from inch.aio.queue.base import AsyncBaseQueue
from inch.aio.queue.memory_queue import AsyncMemoryQueue
from inch.processor.task import Task

T = TypeVar("T")
R = TypeVar("R")

logger = logging.getLogger(__name__)


class AsyncInchPoolExecutor(Generic[T, R]):
    def __init__(
        self,
        queue: AsyncBaseQueue[Task[T, R]] | None = None,
        max_workers: int = 4,
        thread_name_prefix: str = "InchPoolExecutor",
        *,
        show_progress: bool = True,
    ) -> None:
        if not queue:
            queue = AsyncMemoryQueue()
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
        # Always try to use current running loop since we assume async environment
        try:
            self._loop = asyncio.get_running_loop()
            self._loop_thread = None
        except RuntimeError as e:
            msg = "InchPoolExecutor requires a running event loop. Use it within an async context."
            raise RuntimeError(msg) from e

        self._start_workers()

    async def submit(self, fn: Callable[[T], R] | Callable[[T], Awaitable[R]], data: T, *args: object, **kwargs: object) -> R:
        """Submit a task and return the result directly"""
        if self._shutdown:
            msg = "Cannot schedule new futures after shutdown"
            raise RuntimeError(msg)

        future: Future = Future()
        task: Task[T, R] = Task(fn=fn, data=data, future=future, args=args, kwargs=kwargs)

        # Use current event loop directly
        await self._queue.enqueue(task)

        # Update progress
        with self._lock:
            self._submitted_count += 1
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

        # Wait for result using asyncio
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, future.result)
        except Exception:
            # Update failed count when exception occurs
            with self._lock:
                self._failed_count += 1
            raise

    async def map(
        self,
        fn: Callable[[T], R] | Callable[[T], Awaitable[R]],
        iterable: Iterable[T],
    ) -> list[R]:
        """Process items concurrently and return results"""
        items = list(iterable)

        # Update progress with total count
        with self._lock:
            self._submitted_count += len(items)
            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, total=self._submitted_count)

        # Submit tasks without updating progress individually
        tasks = []
        for item in items:
            if self._shutdown:
                msg = "Cannot schedule new futures after shutdown"
                raise RuntimeError(msg)

            future: Future = Future()
            task: Task[T, R] = Task(fn=fn, data=item, future=future, args=(), kwargs={})
            await self._queue.enqueue(task)
            tasks.append(asyncio.get_event_loop().run_in_executor(None, future.result))

        return await asyncio.gather(*tasks)

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        self._shutdown = True

        if cancel_futures:
            asyncio.run_coroutine_threadsafe(self._cancel_pending_tasks(), self._loop)

        if wait:
            for worker in self._workers:
                worker.join()

        # No need to stop the loop since we're using the current running loop

    async def _cancel_pending_tasks(self) -> None:
        while True:
            message = await self._queue.dequeue(visibility_timeout=1)
            if message is None:
                break
            message.data.future.cancel()
            await self._queue.ack(message)

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
                # Get message from queue using the main event loop
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._queue.dequeue(visibility_timeout=60),
                        self._loop,
                    )
                    message = future.result(timeout=1.0)
                except RuntimeError:
                    # Event loop is closed, stop worker
                    break

                if message is None:
                    continue

                task = message.data

                if task.future.cancelled():
                    with contextlib.suppress(RuntimeError):
                        asyncio.run_coroutine_threadsafe(self._queue.ack(message), self._loop)
                    continue

                # Execute task directly in worker thread
                try:
                    if inspect.iscoroutinefunction(task.fn):
                        # Handle async function - run in the main event loop
                        coro = task.fn(task.data, *task.args, **task.kwargs)
                        result_future = asyncio.run_coroutine_threadsafe(coro, self._loop)
                        result = result_future.result()
                    else:
                        # Handle sync function
                        result = task.fn(task.data, *task.args, **task.kwargs)

                    task.future.set_result(result)
                    with contextlib.suppress(RuntimeError):
                        asyncio.run_coroutine_threadsafe(self._queue.ack(message), self._loop)

                    # Update progress
                    with self._lock:
                        self._completed_count += 1
                        if self._progress and self._task_id is not None:
                            self._progress.update(self._task_id, completed=self._completed_count)

                except Exception as e:
                    task.future.set_exception(e)
                    with contextlib.suppress(RuntimeError):
                        asyncio.run_coroutine_threadsafe(self._queue.nack(message, str(e)), self._loop)

                    logger.exception("Task processing failed")

            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("Worker error")

    async def __aenter__(self) -> "AsyncInchPoolExecutor[T, R]":
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

    async def __aexit__(
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
