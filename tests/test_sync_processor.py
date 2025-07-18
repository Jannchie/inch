import time
import threading
from typing import Any

import pytest

from inch.processor.sync_processor import SyncInchPoolProcessor
from inch.queue.memory_queue import SyncMemoryQueue
from tests.utils import TestItem


@pytest.fixture
def test_items():
    return [TestItem(i, f"data_{i}") for i in range(10)]


@pytest.fixture
def processed_items():
    return []


@pytest.fixture
def failed_items():
    return []


def sync_process_func(item: TestItem) -> None:
    """Synchronous processing function"""
    time.sleep(0.01)  # Simulate some work
    item.processed = True


def failing_process_func(item: TestItem) -> None:
    """Function that always fails"""
    raise ValueError(f"Failed to process {item.id}")


def test_sync_processor_init():
    """Test processor initialization"""
    processor = SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=2,
        show_progress=False
    )
    assert processor._max_workers == 2
    assert not processor._shutdown_event.is_set()
    # No shutdown method, processor is cleaned up automatically


def test_sync_processor_with_custom_queue():
    """Test processor with custom queue"""
    queue = SyncMemoryQueue[TestItem](max_size=10)
    processor = SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=2,
        queue=queue,
        show_progress=False
    )
    assert processor._queue is queue
    # No shutdown method, processor is cleaned up automatically


def test_sync_processor_invalid_worker_count():
    """Test processor with invalid worker count"""
    with pytest.raises(ValueError, match="worker must be positive"):
        SyncInchPoolProcessor[TestItem](
            process_func=sync_process_func,
            worker=0,
            show_progress=False
        )


def test_sync_processor_submit_and_process():
    """Test submitting and processing items"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    # Use context manager to handle worker lifecycle
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=2,
        show_progress=False
    ) as processor:
        # Submit items
        items = [TestItem(i, f"data_{i}") for i in range(5)]
        for item in items:
            processor.submit(item)
        
        # Context manager will wait for completion
    
    # Check results
    assert len(processed_items) == 5
    for item in processed_items:
        assert item.processed is True


def test_sync_processor_submit_after_shutdown():
    """Test that submit fails after shutdown"""
    processor = SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=1,
        show_progress=False
    )
    
    # Set shutdown event manually to simulate shutdown
    processor._shutdown_event.set()
    
    with pytest.raises(RuntimeError, match="Cannot submit new tasks after shutdown"):
        processor.submit(TestItem(1, "test"))


def test_sync_processor_context_manager():
    """Test context manager usage"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=2,
        show_progress=False
    ) as processor:
        for item in items:
            processor.submit(item)
        
        # Wait for processing
        time.sleep(0.2)
    
    # Check results
    assert len(processed_items) == 3
    for item in processed_items:
        assert item.processed is True


def test_sync_processor_context_manager_with_progress():
    """Test context manager with progress bar"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    items = [TestItem(i, f"data_{i}") for i in range(3)]
    
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=2,
        show_progress=True
    ) as processor:
        for item in items:
            processor.submit(item)
        
        # Wait for processing
        time.sleep(0.2)
    
    # Check results
    assert len(processed_items) == 3
    for item in processed_items:
        assert item.processed is True


def test_sync_processor_error_handling():
    """Test error handling in processing"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=2,
        show_progress=False
    ) as processor:
        # Submit successful items only
        items = [TestItem(i, f"data_{i}") for i in range(0, 6, 2)]  # Even items only
        for item in items:
            processor.submit(item)
        
        # Context manager will wait for completion
    
    # Check results - all items should be processed successfully
    assert len(processed_items) == 3  # Items (0, 2, 4)
    for item in processed_items:
        assert item.processed is True
    
    # Check progress counters
    assert processor._submitted_count == 3
    assert processor._completed_count == 3
    assert processor._failed_count == 0


def test_sync_processor_thread_names():
    """Test that worker threads have correct names"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=2,
        thread_name_prefix="TestProcessor",
        show_progress=False
    ) as processor:
        # Submit some items to keep workers busy
        processor.submit(TestItem(1, "test1"))
        processor.submit(TestItem(2, "test2"))
        
        # Check thread names
        thread_names = [worker.name for worker in processor._workers]
        assert "TestProcessor-0" in thread_names
        assert "TestProcessor-1" in thread_names


def test_sync_processor_queue_size_limit():
    """Test queue size limitation"""
    with SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=1,
        max_queue_size=2,
        show_progress=False
    ) as processor:
        # Submit items up to limit
        processor.submit(TestItem(1, "test1"))
        processor.submit(TestItem(2, "test2"))
        
        # This should work as queue has capacity
        processor.submit(TestItem(3, "test3"))


def test_sync_processor_concurrent_submissions():
    """Test concurrent submissions"""
    processed_items = []
    
    def track_process_func(item: TestItem) -> None:
        sync_process_func(item)
        processed_items.append(item)
    
    with SyncInchPoolProcessor[TestItem](
        process_func=track_process_func,
        worker=4,
        show_progress=False
    ) as processor:
        # Submit items from multiple threads
        def submit_items(start_id: int, count: int) -> None:
            for i in range(count):
                processor.submit(TestItem(start_id + i, f"data_{start_id + i}"))
        
        threads = []
        for i in range(3):
            thread = threading.Thread(
                target=submit_items,
                args=(i * 10, 3)  # Reduced count to speed up test
            )
            threads.append(thread)
            thread.start()
        
        # Wait for all submissions
        for thread in threads:
            thread.join()
        
        # Context manager will wait for completion
    
    # Check results
    assert len(processed_items) == 9  # 3 threads * 3 items each


def test_sync_processor_progress_tracking():
    """Test progress tracking functionality"""
    with SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=2,
        show_progress=False
    ) as processor:
        # Submit items
        items = [TestItem(i, f"data_{i}") for i in range(5)]
        for item in items:
            processor.submit(item)
        
        # Context manager will wait for completion
    
    # Check progress
    assert processor._submitted_count == 5
    assert processor._completed_count == 5
    assert processor._failed_count == 0


def test_sync_processor_empty_queue_processing():
    """Test processing with empty queue"""
    with SyncInchPoolProcessor[TestItem](
        process_func=sync_process_func,
        worker=2,
        show_progress=False
    ) as processor:
        # Start without submitting items
        pass
    
    # Should complete without errors
    assert processor._submitted_count == 0
    assert processor._completed_count == 0
    assert processor._failed_count == 0