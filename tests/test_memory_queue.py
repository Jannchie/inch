import asyncio
import threading
import time

import pytest

from inch.queue.base import Message, MessageStatus
from inch.queue.memory_queue import SyncMemoryQueue
from inch.aio.queue.memory_queue import AsyncMemoryQueue


@pytest.fixture
def queue():
    return AsyncMemoryQueue(max_retries=1)  # Set max_retries to 1 for easier dead-letter testing


@pytest.mark.asyncio
async def test_enqueue_dequeue_ack(queue):
    await queue.enqueue("task1")
    message = await queue.dequeue(visibility_timeout=1)
    assert message is not None
    assert message.data == "task1"
    assert message.status == MessageStatus.PROCESSING

    status = await queue.get_status()
    assert status.pending_count == 0
    assert status.processing_count == 1

    await queue.ack(message)
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 1


@pytest.mark.asyncio
async def test_enqueue_dequeue_nack(queue):
    await queue.enqueue("task2")
    message = await queue.dequeue(visibility_timeout=1)
    assert message is not None
    assert message.data == "task2"

    await queue.nack(message, error="failed")
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.pending_count == 0  # Should be 0 because max_retries is 1
    assert status.dead_letter_count == 1
    dead_letters = await queue.get_dead_letter_messages()
    assert len(dead_letters) == 1
    assert dead_letters[0].data == "task2"
    assert dead_letters[0].status == MessageStatus.DEAD_LETTER
    assert dead_letters[0].retry_count == 1


@pytest.mark.asyncio
async def test_message_timeout_requeue(queue):
    await queue.enqueue("task3")
    message = await queue.dequeue(visibility_timeout=1)  # 1 second timeout
    assert message is not None
    assert message.data == "task3"

    status = await queue.get_status()
    assert status.processing_count == 1

    await asyncio.sleep(1.1)  # Wait for timeout

    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.pending_count == 0
    assert status.dead_letter_count == 1

    await queue.ack(message)
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 0


@pytest.mark.asyncio
async def test_message_timeout_dead_letter(queue):
    queue.max_retries = 2
    await queue.enqueue("task4")
    message = await queue.dequeue(visibility_timeout=1)
    assert message is not None

    await asyncio.sleep(1.1)  # Timeout once
    requeued_message = await queue.dequeue(visibility_timeout=1)
    assert requeued_message is not None
    assert requeued_message.retry_count == 1

    await asyncio.sleep(1.1)  # Timeout again, should go to dead letter
    # Try to dequeue, should be None as it's in dead letter
    no_message = await queue.dequeue(visibility_timeout=1)
    assert no_message is None

    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 1
    dead_letters = await queue.get_dead_letter_messages()
    assert len(dead_letters) == 1
    assert dead_letters[0].data == "task4"
    assert dead_letters[0].status == MessageStatus.DEAD_LETTER
    assert dead_letters[0].retry_count == 2  # max_retries + 1 (initial dequeue + 2 retries)


@pytest.mark.asyncio
async def test_extend_visibility(queue):
    queue.max_retries = 2
    await queue.enqueue("task5")
    message = await queue.dequeue(visibility_timeout=1)  # 1 second timeout
    assert message is not None

    await asyncio.sleep(0.9)  # Wait past original timeout

    # Extend visibility by 5 seconds
    extended = await queue.extend_visibility(message.message_id, 0.5)
    assert extended is True

    await asyncio.sleep(0.2)  # Wait past original timeout

    # Try to dequeue, should be None as it's still in flight
    no_message = await queue.dequeue(visibility_timeout=1)
    assert no_message is None

    status = await queue.get_status()
    assert status.processing_count == 1

    await asyncio.sleep(1.0)  # Wait for the extended timeout to pass
    print(await queue.get_status())
    # Now it should be re-queued
    requeued_message = await queue.dequeue(visibility_timeout=1)
    assert requeued_message is not None
    assert requeued_message.data == "task5"
    assert requeued_message.retry_count == 1

    await queue.ack(requeued_message)


@pytest.mark.asyncio
async def test_clear_queue(queue):
    await queue.enqueue("task_clear_1")
    await queue.enqueue("task_clear_2")
    msg = await queue.dequeue(visibility_timeout=1)
    await queue.nack(msg)
    await queue.enqueue("task_clear_3")

    status = await queue.get_status()
    assert status.pending_count == 2
    assert status.processing_count == 0
    assert status.dead_letter_count == 1

    await queue.clear()
    status = await queue.get_status()
    assert status.pending_count == 0
    assert status.processing_count == 0
    assert status.success_count == 0
    assert status.dead_letter_count == 0


@pytest.mark.asyncio
async def test_dequeue_empty_queue(queue):
    message = await queue.dequeue(visibility_timeout=1)
    assert message is None
    status = await queue.get_status()
    assert status.pending_count == 0
    assert status.processing_count == 0


@pytest.mark.asyncio
async def test_nack_non_existent_message(queue):
    message = Message(data="non_existent", message_id="fake_id")
    await queue.nack(message, error="test")
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 0


@pytest.mark.asyncio
async def test_nack_retry(queue):
    queue.max_retries = 2
    await queue.enqueue("test")
    message = await queue.dequeue()
    await queue.nack(message, error="test")
    status = await queue.get_status()
    status.pending_count = 1
    await queue.dequeue()
    await queue.nack(message, error="test")
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 1


@pytest.mark.asyncio
async def test_ack_non_existent_message(queue):
    message = Message(data="non_existent", message_id="fake_id")
    await queue.ack(message)
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 0


# Queue capacity limit tests
@pytest.mark.asyncio
async def test_async_queue_capacity_limit_blocking():
    queue = AsyncMemoryQueue(max_size=2)
    
    # Fill the queue to capacity
    await queue.enqueue("task1")
    await queue.enqueue("task2")
    
    # Queue should be full now
    assert queue._is_full()
    
    # Try to enqueue another item - this should not block in the test
    # We'll use asyncio.wait_for to simulate a timeout
    start_time = time.time()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.enqueue("task3"), timeout=0.1)
    elapsed = time.time() - start_time
    assert elapsed >= 0.1  # Should have waited at least the timeout duration


@pytest.mark.asyncio
async def test_async_queue_capacity_limit_with_ack():
    queue = AsyncMemoryQueue(max_size=2)
    
    # Fill the queue to capacity
    await queue.enqueue("task1")
    await queue.enqueue("task2")
    
    # Dequeue and ack one message
    message = await queue.dequeue()
    assert message is not None
    await queue.ack(message)
    
    # Now we should be able to enqueue another item
    await queue.enqueue("task3")
    
    status = await queue.get_status()
    assert status.pending_count == 2  # task2 and task3


def test_sync_queue_capacity_limit_blocking():
    queue = SyncMemoryQueue(max_size=2)
    
    # Fill the queue to capacity
    queue.enqueue("task1")
    queue.enqueue("task2")
    
    # Queue should be full now
    assert queue._is_full()
    
    # Test blocking behavior using threading
    enqueue_completed = threading.Event()
    enqueue_started = threading.Event()
    
    def enqueue_task():
        enqueue_started.set()
        queue.enqueue("task3")  # This should block
        enqueue_completed.set()
    
    # Start enqueue in another thread
    thread = threading.Thread(target=enqueue_task)
    thread.start()
    
    # Wait for the enqueue to start
    enqueue_started.wait(timeout=1.0)
    
    # Give it a short time to try to enqueue (should be blocked)
    time.sleep(0.1)
    assert not enqueue_completed.is_set()  # Should still be blocked
    
    # Dequeue and ack one message to make space
    message = queue.dequeue()
    assert message is not None
    queue.ack(message)
    
    # Now the enqueue should complete
    enqueue_completed.wait(timeout=1.0)
    assert enqueue_completed.is_set()
    
    thread.join()
    
    status = queue.get_status()
    assert status.pending_count == 2  # task2 and task3


def test_sync_queue_capacity_limit_with_nack():
    queue = SyncMemoryQueue(max_size=2, max_retries=1)
    
    # Fill the queue to capacity
    queue.enqueue("task1")
    queue.enqueue("task2")
    
    # Dequeue and nack a message (should go to dead letter)
    message = queue.dequeue()
    assert message is not None
    queue.nack(message)
    queue.nack(message)  # Second nack should send to dead letter
    
    # Now we should be able to enqueue another item
    queue.enqueue("task3")
    
    status = queue.get_status()
    assert status.pending_count == 2  # task2 and task3
    assert status.dead_letter_count == 1  # task1


def test_queue_unlimited_capacity():
    # Test that None max_size means unlimited
    queue = SyncMemoryQueue(max_size=None)
    
    # Should be able to enqueue many items
    for i in range(1000):
        queue.enqueue(f"task{i}")
    
    status = queue.get_status()
    assert status.pending_count == 1000
    assert not queue._is_full()


# Priority tests
@pytest.mark.asyncio
async def test_async_priority_queue():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages with different priorities
    await queue.enqueue("low", priority=1)
    await queue.enqueue("high", priority=10)
    await queue.enqueue("medium", priority=5)
    await queue.enqueue("highest", priority=15)
    
    # Dequeue should return highest priority first
    message1 = await queue.dequeue()
    assert message1 is not None
    assert message1.data == "highest"
    assert message1.priority == 15
    
    message2 = await queue.dequeue()
    assert message2 is not None
    assert message2.data == "high"
    assert message2.priority == 10
    
    message3 = await queue.dequeue()
    assert message3 is not None
    assert message3.data == "medium"
    assert message3.priority == 5
    
    message4 = await queue.dequeue()
    assert message4 is not None
    assert message4.data == "low"
    assert message4.priority == 1


def test_sync_priority_queue():
    queue = SyncMemoryQueue()
    
    # Enqueue messages with different priorities
    queue.enqueue("low", priority=1)
    queue.enqueue("high", priority=10)
    queue.enqueue("medium", priority=5)
    queue.enqueue("highest", priority=15)
    
    # Dequeue should return highest priority first
    message1 = queue.dequeue()
    assert message1 is not None
    assert message1.data == "highest"
    assert message1.priority == 15
    
    message2 = queue.dequeue()
    assert message2 is not None
    assert message2.data == "high"
    assert message2.priority == 10
    
    message3 = queue.dequeue()
    assert message3 is not None
    assert message3.data == "medium"
    assert message3.priority == 5
    
    message4 = queue.dequeue()
    assert message4 is not None
    assert message4.data == "low"
    assert message4.priority == 1


# Batch operation tests
@pytest.mark.asyncio
async def test_async_enqueue_batch():
    queue = AsyncMemoryQueue()
    
    # Enqueue batch of items with priorities
    items = [("task1", 5), ("task2", 10), ("task3", 1)]
    await queue.enqueue_batch(items)
    
    status = await queue.get_status()
    assert status.pending_count == 3
    
    # Should dequeue in priority order
    message1 = await queue.dequeue()
    assert message1 is not None
    assert message1.data == "task2"  # priority 10
    
    message2 = await queue.dequeue()
    assert message2 is not None
    assert message2.data == "task1"  # priority 5
    
    message3 = await queue.dequeue()
    assert message3 is not None
    assert message3.data == "task3"  # priority 1


@pytest.mark.asyncio
async def test_async_dequeue_batch():
    queue = AsyncMemoryQueue()
    
    # Enqueue multiple messages
    for i in range(5):
        await queue.enqueue(f"task{i}", priority=i)
    
    # Dequeue batch
    messages = await queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Should be in priority order (highest first)
    assert messages[0].data == "task4"  # priority 4
    assert messages[1].data == "task3"  # priority 3
    assert messages[2].data == "task2"  # priority 2
    
    status = await queue.get_status()
    assert status.pending_count == 2
    assert status.processing_count == 3


@pytest.mark.asyncio
async def test_async_ack_batch():
    queue = AsyncMemoryQueue()
    
    # Enqueue and dequeue multiple messages
    for i in range(3):
        await queue.enqueue(f"task{i}")
    
    messages = await queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Ack all messages at once
    await queue.ack_batch(messages)
    
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 3


@pytest.mark.asyncio
async def test_async_nack_batch():
    queue = AsyncMemoryQueue(max_retries=1)
    
    # Enqueue and dequeue multiple messages
    for i in range(3):
        await queue.enqueue(f"task{i}")
    
    messages = await queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Nack all messages at once
    await queue.nack_batch(messages, error="batch error")
    
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 3  # All should go to dead letter with max_retries=1


def test_sync_enqueue_batch():
    queue = SyncMemoryQueue()
    
    # Enqueue batch of items with priorities
    items = [("task1", 5), ("task2", 10), ("task3", 1)]
    queue.enqueue_batch(items)
    
    status = queue.get_status()
    assert status.pending_count == 3
    
    # Should dequeue in priority order
    message1 = queue.dequeue()
    assert message1 is not None
    assert message1.data == "task2"  # priority 10
    
    message2 = queue.dequeue()
    assert message2 is not None
    assert message2.data == "task1"  # priority 5
    
    message3 = queue.dequeue()
    assert message3 is not None
    assert message3.data == "task3"  # priority 1


def test_sync_dequeue_batch():
    queue = SyncMemoryQueue()
    
    # Enqueue multiple messages
    for i in range(5):
        queue.enqueue(f"task{i}", priority=i)
    
    # Dequeue batch
    messages = queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Should be in priority order (highest first)
    assert messages[0].data == "task4"  # priority 4
    assert messages[1].data == "task3"  # priority 3
    assert messages[2].data == "task2"  # priority 2
    
    status = queue.get_status()
    assert status.pending_count == 2
    assert status.processing_count == 3


def test_sync_ack_batch():
    queue = SyncMemoryQueue()
    
    # Enqueue and dequeue multiple messages
    for i in range(3):
        queue.enqueue(f"task{i}")
    
    messages = queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Ack all messages at once
    queue.ack_batch(messages)
    
    status = queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 3


def test_sync_nack_batch():
    queue = SyncMemoryQueue(max_retries=1)
    
    # Enqueue and dequeue multiple messages
    for i in range(3):
        queue.enqueue(f"task{i}")
    
    messages = queue.dequeue_batch(limit=3)
    assert len(messages) == 3
    
    # Nack all messages at once
    queue.nack_batch(messages, error="batch error")
    
    status = queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 3  # All should go to dead letter with max_retries=1
