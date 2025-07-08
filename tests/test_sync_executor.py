import time
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

import pytest

from inch.executor.sync_executor import InchPoolExecutor
from inch.queue.memory_queue import SyncMemoryQueue


@dataclass
class TestItem:
    id: int
    data: str
    processed: bool = False


@pytest.fixture
def test_items():
    return [TestItem(i, f"data_{i}") for i in range(10)]


def sync_process_func(item: TestItem) -> TestItem:
    """Synchronous processing function"""
    time.sleep(0.01)  # Simulate some work
    item.processed = True
    return item


def sync_only_func(item: TestItem) -> TestItem:
    """Synchronous only function"""
    time.sleep(0.01)  # Simulate some work
    item.processed = True
    return item


def failing_process_func(item: TestItem) -> TestItem:
    """Function that always fails"""
    raise ValueError(f"Failed to process {item.id}")


def test_sync_executor_init():
    """Test executor initialization"""
    executor = InchPoolExecutor[TestItem, TestItem](max_workers=2)
    assert executor._max_workers == 2
    assert not executor._shutdown
    assert len(executor._workers) == 2
    executor.shutdown()


def test_sync_executor_with_custom_queue():
    """Test executor with custom queue"""
    queue = SyncMemoryQueue[Any]()
    executor = InchPoolExecutor[TestItem, TestItem](
        queue=queue,
        max_workers=2,
        show_progress=False
    )
    assert executor._queue is queue
    executor.shutdown()


def test_sync_executor_submit_sync_function():
    """Test submitting synchronous function"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    result = executor.submit(sync_only_func, item)
    
    assert result.processed is True
    assert result.id == 1
    executor.shutdown()


def test_sync_executor_submit_with_args_kwargs():
    """Test submitting function with additional args and kwargs"""
    executor = InchPoolExecutor[TestItem, str](
        max_workers=1,
        show_progress=False
    )
    
    def process_with_args(item: TestItem, prefix: str, suffix: str = "end") -> str:
        return f"{prefix}_{item.data}_{suffix}"
    
    item = TestItem(1, "test")
    result = executor.submit(process_with_args, item, "start", suffix="finish") # type: ignore
    
    assert result == "start_test_finish"
    executor.shutdown()


def test_sync_executor_submit_failure():
    """Test submitting function that fails"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    
    with pytest.raises(ValueError, match="Failed to process 1"):
        executor.submit(failing_process_func, item)
    
    executor.shutdown()


def test_sync_executor_map():
    """Test map functionality"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(5)]
    results = executor.map(sync_only_func, items)
    
    assert len(results) == 5
    for result in results:
        assert result.processed is True
    
    executor.shutdown()


def test_sync_executor_map_empty():
    """Test map with empty iterable"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    results = executor.map(sync_only_func, [])
    
    assert len(results) == 0
    executor.shutdown()


def test_sync_executor_shutdown_after_submit():
    """Test that submit fails after shutdown"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    executor.shutdown()
    
    item = TestItem(1, "test")
    with pytest.raises(RuntimeError, match="Cannot schedule new futures after shutdown"):
        executor.submit(sync_only_func, item)


def test_sync_executor_shutdown_after_map():
    """Test that map fails after shutdown"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    executor.shutdown()
    
    items = [TestItem(1, "test")]
    with pytest.raises(RuntimeError, match="Cannot schedule new futures after shutdown"):
        executor.map(sync_only_func, items)


def test_sync_executor_shutdown_with_cancel():
    """Test shutdown with cancel_futures=True"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    # Submit some tasks but don't wait for them
    def slow_task(item: TestItem) -> TestItem:
        time.sleep(0.1)
        return item
    
    # Submit tasks in threads
    threads = []
    for i in range(2):
        thread = threading.Thread(
            target=lambda: executor.submit(slow_task, TestItem(i, f"data_{i}"))
        )
        thread.start()
        threads.append(thread)
    
    # Give time for tasks to be queued
    time.sleep(0.05)
    
    # Shutdown with cancel
    executor.shutdown(cancel_futures=True)
    
    # Wait for threads to complete
    for thread in threads:
        thread.join(timeout=0.5)


def test_sync_executor_context_manager():
    """Test context manager usage"""
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    with InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    ) as executor:
        results = executor.map(sync_only_func, items)
        
        assert len(results) == 3
        for result in results:
            assert result.processed is True


def test_sync_executor_context_manager_with_progress():
    """Test context manager with progress bar"""
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    with InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=True
    ) as executor:
        results = executor.map(sync_only_func, items)
        
        assert len(results) == 3
        for result in results:
            assert result.processed is True


def test_sync_executor_concurrent_operations():
    """Test concurrent submit and map operations"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=4,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    # Use threading to test concurrent operations
    results = []
    
    def submit_task():
        result = executor.submit(sync_only_func, TestItem(100, "submit"))
        results.append(("submit", result))
    
    def map_task():
        map_results = executor.map(sync_only_func, items)
        results.append(("map", map_results))
    
    # Start concurrent operations
    thread1 = threading.Thread(target=submit_task)
    thread2 = threading.Thread(target=map_task)
    
    thread1.start()
    thread2.start()
    
    thread1.join()
    thread2.join()
    
    assert len(results) == 2
    
    # Check results
    submit_result = None
    map_results = None
    for result_type, result in results:
        if result_type == "submit":
            submit_result = result
        elif result_type == "map":
            map_results = result
    
    assert submit_result is not None
    assert submit_result.processed is True
    assert submit_result.id == 100
    
    assert map_results is not None
    assert len(map_results) == 3
    for result in map_results:
        assert result.processed is True
    
    executor.shutdown()


def test_sync_executor_progress_tracking():
    """Test progress tracking functionality"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    # Submit some tasks
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    executor.map(sync_only_func, items)
    
    # Check progress counters
    assert executor._submitted_count == 3
    assert executor._completed_count == 3
    assert executor._failed_count == 0
    
    executor.shutdown()


def test_sync_executor_failed_task_counting():
    """Test failed task counting"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(2)]
    
    # Submit one successful task
    executor.submit(sync_only_func, items[0])
    
    # Submit one failing task
    try:
        executor.submit(failing_process_func, items[1])
    except ValueError:
        pass
    
    # Check counters
    assert executor._submitted_count == 2
    assert executor._completed_count == 1
    assert executor._failed_count == 1
    
    executor.shutdown()


def test_sync_executor_thread_names():
    """Test that worker threads have correct names"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        thread_name_prefix="TestExecutor",
        show_progress=False
    )
    
    # Check thread names
    thread_names = [worker.name for worker in executor._workers]
    assert "TestExecutor-0" in thread_names
    assert "TestExecutor-1" in thread_names
    
    executor.shutdown()


def test_sync_executor_shutdown_wait():
    """Test shutdown with wait=True"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    # Submit a task
    executor.submit(sync_only_func, TestItem(1, "test"))
    
    # Shutdown and wait
    executor.shutdown(wait=True)
    
    # Check that all workers have stopped
    for worker in executor._workers:
        assert not worker.is_alive()


def test_sync_executor_shutdown_no_wait():
    """Test shutdown with wait=False"""
    executor = InchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    # Submit a task
    executor.submit(sync_only_func, TestItem(1, "test"))
    
    # Shutdown without waiting
    executor.shutdown(wait=False)
    
    # Workers might still be running
    # But executor should be marked as shutdown
    assert executor._shutdown is True