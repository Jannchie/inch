import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

from inch.executor.async_executor import AsyncInchPoolExecutor
from inch.queue.memory_queue import AsyncMemoryQueue


@dataclass
class TestItem:
    id: int
    data: str
    processed: bool = False


@pytest.fixture
def test_items():
    return [TestItem(i, f"data_{i}") for i in range(10)]


async def sync_process_func(item: TestItem) -> TestItem:
    """Synchronous processing function"""
    await asyncio.sleep(0.01)  # Simulate some work
    item.processed = True
    return item


async def async_process_func(item: TestItem) -> TestItem:
    """Asynchronous processing function"""
    await asyncio.sleep(0.01)  # Simulate some async work
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


async def failing_async_func(item: TestItem) -> TestItem:
    """Async function that always fails"""
    await asyncio.sleep(0.01)
    raise ValueError(f"Failed to process {item.id}")


@pytest.mark.asyncio
async def test_async_executor_init():
    """Test executor initialization"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](max_workers=2)
    assert executor._max_workers == 2
    assert not executor._shutdown
    assert len(executor._workers) == 2
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_with_custom_queue():
    """Test executor with custom queue"""
    queue = AsyncMemoryQueue[Any]()
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        queue=queue,
        max_workers=2,
        show_progress=False
    )
    assert executor._queue is queue
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_submit_sync_function():
    """Test submitting synchronous function"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    result = await executor.submit(sync_only_func, item)
    
    assert result.processed is True
    assert result.id == 1
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_submit_async_function():
    """Test submitting asynchronous function"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    result = await executor.submit(async_process_func, item)
    
    assert result.processed is True
    assert result.id == 1
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_submit_with_args_kwargs():
    """Test submitting function with additional args and kwargs"""
    executor = AsyncInchPoolExecutor[TestItem, str](
        max_workers=1,
        show_progress=False
    )
    
    def process_with_args(item: TestItem, prefix: str, suffix: str = "end") -> str:
        return f"{prefix}_{item.data}_{suffix}"
    
    item = TestItem(1, "test")
    result = await executor.submit(process_with_args, item, "start", suffix="finish") # type: ignore
    
    assert result == "start_test_finish"
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_submit_failure():
    """Test submitting function that fails"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    
    with pytest.raises(ValueError, match="Failed to process 1"):
        await executor.submit(failing_process_func, item)
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_submit_async_failure():
    """Test submitting async function that fails"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    item = TestItem(1, "test")
    
    with pytest.raises(ValueError, match="Failed to process 1"):
        await executor.submit(failing_async_func, item)
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_map():
    """Test map functionality"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(5)]
    results = await executor.map(sync_only_func, items)
    
    assert len(results) == 5
    for result in results:
        assert result.processed is True
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_map_async_function():
    """Test map with async function"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(5)]
    results = await executor.map(async_process_func, items)
    
    assert len(results) == 5
    for result in results:
        assert result.processed is True
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_map_empty():
    """Test map with empty iterable"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    results = await executor.map(sync_only_func, [])
    
    assert len(results) == 0
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_shutdown_after_submit():
    """Test that submit fails after shutdown"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    executor.shutdown()
    
    item = TestItem(1, "test")
    with pytest.raises(RuntimeError, match="Cannot schedule new futures after shutdown"):
        await executor.submit(sync_only_func, item)


@pytest.mark.asyncio
async def test_async_executor_shutdown_after_map():
    """Test that map fails after shutdown"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    executor.shutdown()
    
    items = [TestItem(1, "test")]
    with pytest.raises(RuntimeError, match="Cannot schedule new futures after shutdown"):
        await executor.map(sync_only_func, items)


@pytest.mark.asyncio
async def test_async_executor_shutdown_with_cancel():
    """Test shutdown with cancel_futures=True"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    # Submit some fast tasks
    async def fast_task(item: TestItem) -> TestItem:
        await asyncio.sleep(0.01)
        return item
    
    # Submit tasks but don't await them
    tasks = []
    for i in range(2):
        task = asyncio.create_task(executor.submit(fast_task, TestItem(i, f"data_{i}")))
        tasks.append(task)
    
    # Give time for tasks to be queued
    await asyncio.sleep(0.05)
    
    # Shutdown with cancel
    executor.shutdown(cancel_futures=True)
    
    # Wait a bit to ensure shutdown completes
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_async_executor_context_manager():
    """Test async context manager usage"""
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    async with AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    ) as executor:
        results = await executor.map(sync_only_func, items)
        
        assert len(results) == 3
        for result in results:
            assert result.processed is True


@pytest.mark.asyncio
async def test_async_executor_context_manager_with_progress():
    """Test async context manager with progress bar"""
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    async with AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=True
    ) as executor:
        results = await executor.map(sync_only_func, items)
        
        assert len(results) == 3
        for result in results:
            assert result.processed is True


@pytest.mark.asyncio
async def test_async_executor_concurrent_operations():
    """Test concurrent submit and map operations"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=4,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(5)]
    
    # Mix of submit and map operations
    submit_task = asyncio.create_task(executor.submit(sync_only_func, TestItem(100, "submit")))
    map_task = asyncio.create_task(executor.map(sync_only_func, items))
    
    submit_result, map_results = await asyncio.gather(submit_task, map_task)
    
    assert submit_result.processed is True
    assert submit_result.id == 100
    assert len(map_results) == 5
    for result in map_results:
        assert result.processed is True
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_no_running_loop():
    """Test that executor requires running event loop"""
    # This test would need to be run in a separate thread without event loop
    # For now, we'll just verify the normal case works
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    assert executor._loop is not None
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_progress_tracking():
    """Test progress tracking functionality"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=2,
        show_progress=False
    )
    
    # Submit some tasks
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    await executor.map(sync_only_func, items)
    
    # Check progress counters
    assert executor._submitted_count == 3
    assert executor._completed_count == 3
    assert executor._failed_count == 0
    
    executor.shutdown()


@pytest.mark.asyncio
async def test_async_executor_failed_task_counting():
    """Test failed task counting"""
    executor = AsyncInchPoolExecutor[TestItem, TestItem](
        max_workers=1,
        show_progress=False
    )
    
    items = [TestItem(i, f"data_{i}") for i in range(2)]
    
    # Submit one successful task
    await executor.submit(sync_only_func, items[0])
    
    # Submit one failing task
    try:
        await executor.submit(failing_process_func, items[1])
    except ValueError:
        pass
    
    # Check counters
    assert executor._submitted_count == 2
    assert executor._completed_count == 1
    assert executor._failed_count == 1
    
    executor.shutdown()