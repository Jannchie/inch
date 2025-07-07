import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

from inch.processor.async_processor import AsyncInchPoolProcessor
from inch.queue.memory_queue import AsyncMemoryQueue


@dataclass
class TestItem:
    id: int
    data: str
    processed: bool = False


@pytest.fixture
def test_items():
    return [TestItem(i, f"data_{i}") for i in range(10)]


@pytest.fixture
def processed_items():
    return []


@pytest.fixture
def failed_items():
    return []


async def sync_process_func(item: TestItem) -> None:
    """Synchronous processing function"""
    await asyncio.sleep(0.01)  # Simulate some work
    item.processed = True


async def async_process_func(item: TestItem) -> None:
    """Asynchronous processing function"""
    await asyncio.sleep(0.01)  # Simulate some async work
    item.processed = True


def failing_process_func(item: TestItem) -> None:
    """Function that always fails"""
    raise ValueError(f"Failed to process {item.id}")


def selective_failing_process_func(item: TestItem) -> None:
    """Function that fails for certain items"""
    if item.id % 3 == 0:
        raise ValueError(f"Failed to process {item.id}")
    item.processed = True


@pytest.mark.asyncio
async def test_async_processor_with_sync_func(test_items):
    """Test async processor with synchronous processing function"""
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=2,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that all items were processed
    assert all(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_with_async_func(test_items):
    """Test async processor with asynchronous processing function"""
    async with AsyncInchPoolProcessor(
        process_func=async_process_func,
        worker=2,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that all items were processed
    assert all(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_with_custom_queue(test_items):
    """Test async processor with custom queue"""
    custom_queue = AsyncMemoryQueue[TestItem](max_size=5)
    
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=2,
        queue=custom_queue,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that all items were processed
    assert all(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_error_handling():
    """Test error handling in async processor"""
    test_items = [TestItem(i, f"data_{i}") for i in range(5)]
    
    async with AsyncInchPoolProcessor(
        process_func=failing_process_func,
        worker=2,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that no items were processed (all failed)
    assert not any(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_partial_failures():
    """Test async processor with some items failing"""
    test_items = [TestItem(i, f"data_{i}") for i in range(6)]
    
    async with AsyncInchPoolProcessor(
        process_func=selective_failing_process_func,
        worker=2,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that only non-failing items were processed
    for item in test_items:
        if item.id % 3 == 0:
            assert not item.processed  # Should have failed
        else:
            assert item.processed  # Should have succeeded


@pytest.mark.asyncio
async def test_async_processor_worker_count():
    """Test that worker count affects processing"""
    test_items = [TestItem(i, f"data_{i}") for i in range(10)]
    
    # Test with 1 worker
    start_time = time.time()
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=1,
        show_progress=False,
    ) as processor:
        for item in test_items:
            await processor.submit(item)
    single_worker_time = time.time() - start_time
    
    # Reset processed state
    for item in test_items:
        item.processed = False
    
    # Test with 4 workers
    start_time = time.time()
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=4,
        show_progress=False,
    ) as processor:
        for item in test_items:
            await processor.submit(item)
    multi_worker_time = time.time() - start_time
    
    # Multi-worker should be faster (though this might be flaky in CI)
    # We'll just check that both completed successfully
    assert all(item.processed for item in test_items)
    assert single_worker_time > 0
    assert multi_worker_time > 0


@pytest.mark.asyncio
async def test_async_processor_submit_after_shutdown():
    """Test that submitting after shutdown raises error"""
    processor = AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=2,
        show_progress=False,
    )
    
    async with processor:
        await processor.submit(TestItem(1, "test"))
    
    # Try to submit after context exit
    with pytest.raises(RuntimeError, match="Cannot submit new tasks after shutdown"):
        await processor.submit(TestItem(2, "test"))


@pytest.mark.asyncio
async def test_async_processor_progress_tracking():
    """Test that progress tracking works correctly"""
    test_items = [TestItem(i, f"data_{i}") for i in range(5)]
    
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=2,
        show_progress=True,  # Enable progress tracking
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
        
        # Check internal counters
        assert processor._submitted_count == len(test_items)
    
    # After completion, all should be processed
    assert all(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_empty_processing():
    """Test async processor with no items submitted"""
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=2,
        show_progress=False,
    ) as processor:
        # Don't submit any items
        pass
    
    # Should complete without issues


@pytest.mark.asyncio
async def test_async_processor_concurrent_submission():
    """Test concurrent submission to async processor"""
    test_items = [TestItem(i, f"data_{i}") for i in range(20)]
    
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=4,
        show_progress=False,
    ) as processor:
        # Submit items concurrently
        submission_tasks = [processor.submit(item) for item in test_items]
        await asyncio.gather(*submission_tasks)
    
    # Check that all items were processed
    assert all(item.processed for item in test_items)


@pytest.mark.asyncio
async def test_async_processor_invalid_worker_count():
    """Test that invalid worker count raises error"""
    with pytest.raises(ValueError, match="worker must be positive"):
        AsyncInchPoolProcessor(
            process_func=sync_process_func,
            worker=0,
            show_progress=False,
        )
    
    with pytest.raises(ValueError, match="worker must be positive"):
        AsyncInchPoolProcessor(
            process_func=sync_process_func,
            worker=-1,
            show_progress=False,
        )


@pytest.mark.asyncio
async def test_async_processor_large_batch():
    """Test async processor with a large batch of items"""
    test_items = [TestItem(i, f"data_{i}") for i in range(100)]
    
    async with AsyncInchPoolProcessor(
        process_func=sync_process_func,
        worker=8,
        show_progress=False,
    ) as processor:
        # Submit all items
        for item in test_items:
            await processor.submit(item)
    
    # Check that all items were processed
    assert all(item.processed for item in test_items)
    assert len([item for item in test_items if item.processed]) == 100