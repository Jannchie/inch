import atexit
import logging
import math
import queue
import random
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, Generic, overload

from inch.processor.logger import logger
from inch.types import TaskType


class InchProcessor(Generic[TaskType]):
    def __init__(
        self,
        target: Callable[[list[TaskType]], Any],
        batch_size: int = 1,
        max_worker: int = 1,
    ) -> None:
        """
        Initialize an InchProcessor instance.
        Creates a thread-safe queue for storing tasks and initializes the stop event
        and active consumer counter.

        Args:
            target: The callable to process tasks. If batch_size is None, it will receive
                   a single TaskType item. Otherwise, it will receive a list[TaskType].
            batch_size: The number of tasks to process in a batch. If None, tasks are processed
                       one at a time.
            max_worker: The number of worker threads to create. Default is 1.
        """

        # Use the TaskType type variable to specify the type of elements in the queue
        self._task_queue: queue.Queue[TaskType] = queue.Queue()
        self._stop_event = threading.Event()
        self._batch_size = batch_size
        self._max_worker = max_worker
        self._target = target
        self._consuming_count = 0
        self._consuming_lock = threading.Lock()
        # Register signal handlers to gracefully stop on interruption
        self._setup_signal_handlers()

        for _ in range(self._max_worker):
            # Create a consumer thread for each worker
            thread = threading.Thread(target=self.consume_wrapper, daemon=True)
            thread.start()

    def consume_wrapper(self) -> None:
        while self.is_running():
            batch = self._get(self._batch_size)

            if batch is not None:
                self._target(batch)

            with self._consuming_lock:
                self._consuming_count -= len(batch)

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers to gracefully stop the processor on interruption."""

        def signal_handler(*_arg: object, **_kwargs: object) -> None:
            if not self.is_running():
                return
            logger.info("Received stop signal, stopping processor...")
            self.stop(wait_for_completion=False)

        # Register handlers for common interrupt signals
        signal.signal(signal.SIGINT, signal_handler)  # KeyboardInterrupt (Ctrl+C)
        if sys.platform != "win32":  # SIGTERM is not available on Windows
            signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

        atexit.register(signal_handler)

    def put(self, task: TaskType) -> None:
        """
        Called by producers to add a task of type `TaskType` to the queue.

        Args:
            task (TaskType): The task to add.

        Raises:
            RuntimeError: If the processor has been stopped, new tasks are not allowed.
        """
        if not self.is_running():
            msg = "Processor has been stopped, cannot add new tasks."
            raise RuntimeError(msg)
        self._task_queue.put(task)

    def _get_task_batch(self, batch_size: int | None = None) -> list[TaskType]:
        """
        Internal method that tries to get a batch of tasks of type `List[TaskType]`.
        Blocks until batch_size or timeout conditions are met, or a stop signal is received.

        Args:
            batch_size (Optional[int]): Specifies the batch size for this retrieval.
                                      If not provided, uses the instance's default value.
        """
        batch_size = 1 if batch_size is None else batch_size
        if batch_size <= 0:
            msg = "Batch size must be greater than 0"
            raise ValueError(msg)

        # Specify the type of the batch list
        batch: list[TaskType] = []
        attempt = 0

        while len(batch) < batch_size and self.is_running():
            try:
                with self._consuming_lock:
                    task: TaskType = self._task_queue.get(block=False)
                    batch.append(task)
                    self._consuming_count += 1
                # Reset attempt counter after successful retrieval
                attempt = 0
            except queue.Empty:  # noqa: PERF203
                if batch:
                    # If we have a partial batch, return it
                    return batch
                # Apply exponential backoff when queue is empty
                backoff_time = self._calculate_backoff_time(attempt, max_backoff=1.0, base=2.0)
                time.sleep(backoff_time)
                attempt += 1

        return batch

    @overload
    def _get(self) -> TaskType | None:
        """
        Called by consumers to get a single task.

        This method blocks until a task is available,
        or the wait time exceeds timeout, or the processor is stopped and the queue is empty.

        Returns:
            TaskType | None: The retrieved task, or None if the processor has been stopped
                            and there are no more tasks in the queue or timeout occurred.
        """

    @overload
    def _get(self, batch_size: int) -> list[TaskType]:
        """
        Called by consumers to get a batch of tasks of type `List[TaskType]`.

        This method blocks until at least one task is available and the batch size is reached,
        or the wait time exceeds timeout, or the processor is stopped and the queue is empty.

        Args:
            batch_size (int): Specifies the batch size for this retrieval. Must be greater than 0.

        Returns:
            List[TaskType]: The retrieved batch of tasks.
                           If the processor has been stopped and there are no more tasks in the queue,
                           returns an empty list.
        """

    def _get(self, batch_size: int | None = None) -> TaskType | list[TaskType] | None:
        """
        Called by consumers to get a task or batch of tasks.

        - When batch_size is not provided or is None, returns a single task (TaskType) or None
        - When batch_size is provided, returns a batch of tasks of type List[TaskType]

        This method blocks until a task is available,
        or the wait time exceeds timeout, or the processor is stopped and the queue is empty.

        Args:
            batch_size (Optional[int]): Specifies the batch size for this retrieval.
                                      If not provided, gets a single task.

        Returns:
            Union[TaskType, List[TaskType], None]:
                - When batch_size is not provided: The retrieved task, or None if the processor
                  has been stopped/timed out
                - When batch_size is provided: The retrieved batch of tasks, or an empty list
                  if the processor has been stopped/timed out
        """
        # Single task mode
        if batch_size is not None:
            return self._retrieve_task_batch(batch_size)
        while self.is_running():
            if batch := self._get_task_batch(1):
                return batch[0]  # Return single element
            if not self.is_running():
                return None

        # Processor has been stopped
        if self._task_queue.empty():
            return None
        final_batch = self._get_task_batch(1)
        return final_batch[0] if final_batch else None

    def _retrieve_task_batch(self, batch_size: int | None = None) -> list[TaskType]:
        while self.is_running():
            if batch := self._get_task_batch(batch_size):
                return batch
            if not self.is_running():
                return []
            continue
        if self._task_queue.empty():
            return []
        final_batch: list[TaskType] = self._get_task_batch(batch_size)
        return final_batch

    def _calculate_backoff_time(self, attempt: int, max_backoff: float = 1.0, base: float = 2.0) -> float:
        """
        Calculate the exponential backoff time for retries.

        Args:
            attempt (int): The current attempt number (starting from 0).
            max_backoff (float): Maximum backoff time in seconds. Default is 1.0.
            base (float): The base for exponential calculation. Default is 2.0.

        Returns:
            float: The time to wait in seconds before the next retry.
        """
        # Calculate exponential backoff with some jitter (randomness)
        jitter = random.random() * 0.1  # 10% randomness
        max_attempt = math.ceil(math.log(max_backoff / 0.01, base))
        return min(max_backoff, (base ** min(attempt, max_attempt)) * 0.01) + jitter

    def stop(self, *, wait_for_completion: bool = True, drain_timeout: float | None = None) -> None:
        """
        Stop the InchProcessor.

        Sets the stop flag to prevent new tasks from being added.
        Optionally waits for all current tasks to be processed by consumers.

        Args:
            wait_for_completion (bool): If True, waits for the queue to become empty and all
                                       active consumers to finish processing their current
                                       batch (to return from get_batch).
            drain_timeout (Optional[float]): If wait_for_completion is True,
                                           this is the maximum time (in seconds) to wait
                                           for the queue to drain and consumers to exit.
        """

        logger.debug("Prevented new task submissions.")

        if wait_for_completion:
            logger.debug("Waiting for existing tasks to complete...")
            start_wait = time.monotonic()
            while True:
                qsize = self._task_queue.qsize()
                if not self.is_running():
                    logger.debug("Stop event set, exiting wait loop.")
                    break
                if qsize == 0 and self._consuming_count == 0:
                    logger.debug("Queue is empty, all consumers have finished.")
                    break
                if drain_timeout is not None and time.monotonic() - start_wait > drain_timeout:
                    logger.debug("Wait timeout (%d seconds), forcing exit.", drain_timeout)
                    break
            logger.debug("Stop completed.")
        else:
            logger.debug("Stop requested, but not waiting for completion.")
        qsize = self.qsize()
        if qsize > 0:
            logger.warning("Processor stopped with %d tasks remaining in the queue.", qsize)

    def qsize(self) -> int:
        """Returns the approximate number of tasks in the queue."""
        return self._task_queue.qsize()

    def is_running(self) -> bool:
        """Checks if the processor is still running (stop flag not set)."""
        return not self._stop_event.is_set() or self._consuming_count > 0

    def __enter__(self) -> "InchProcessor[TaskType]":
        """
        Enter the context manager, returning the processor instance itself.

        Returns:
            InchProcessor[TaskType]: The processor instance itself.
        """
        logger.debug("Entering context manager.")
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """
        Exit the context manager, automatically calling the stop() method.

        Parameters exc_type, exc_val, exc_tb are required by the context manager protocol
        for handling potential exceptions.

        Args:
            exc_type: Exception type, or None if no exception occurred.
            exc_val: Exception value, or None if no exception occurred.
            exc_tb: Exception traceback, or None if no exception occurred.
        """
        self.stop(wait_for_completion=True, drain_timeout=math.inf)


if __name__ == "__main__":
    from rich.logging import RichHandler

    logging.basicConfig(
        level=logging.DEBUG,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_time=False)],
    )
    TIMEOUT = 5
    NUM_CONSUMERS = 2
    TASKS_PER_PRODUCER = 20

    # When creating an InchProcessor instance, the type checker can usually infer the type,
    # but it's better to specify explicitly: InchProcessor[str]
    # with InchProcessor[str]() as processor:
    #     for _ in range(NUM_CONSUMERS):
    #         # Pass typed processor and matching processing function
    #         thread = threading.Thread(target=consumer_func, args=(processor,), daemon=True)
    #         thread.start()

    #     for i in range(TASKS_PER_PRODUCER):
    #         task = f"Task-{i}"
    #         processor.put(task)

    def c(batch: list[str]) -> None:
        logger.debug("Processing batch: %s", batch)

    processor = InchProcessor[str](c, batch_size=4)
    for i in range(15):
        task = f"Task-{i}"
        processor.put(task)

    processor.stop(wait_for_completion=True)
    # processor.stop(wait_for_completion=True)
    logger.debug("Main thread: Example run complete.")
