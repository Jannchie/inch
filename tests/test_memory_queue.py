import asyncio
import threading
import time
import uuid

import pytest

from inch.types import Message, MessageStatus
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

    await queue.ack(message.message_id)
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.success_count == 1


@pytest.mark.asyncio
async def test_enqueue_dequeue_nack(queue):
    await queue.enqueue("task2")
    message = await queue.dequeue(visibility_timeout=1)
    assert message is not None
    assert message.data == "task2"

    await queue.nack(message.message_id, error="failed")
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

    await queue.ack(message.message_id)
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

    await queue.ack(requeued_message.message_id)


@pytest.mark.asyncio
async def test_clear_queue(queue):
    await queue.enqueue("task_clear_1")
    await queue.enqueue("task_clear_2")
    msg = await queue.dequeue(visibility_timeout=1)
    await queue.nack(msg.message_id)
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
    message = Message(data="non_existent", message_id=uuid.uuid4())
    await queue.nack(message.message_id, error="test")
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 0


@pytest.mark.asyncio
async def test_nack_retry(queue):
    queue.max_retries = 2
    await queue.enqueue("test")
    message = await queue.dequeue()
    await queue.nack(message.message_id, error="test")
    status = await queue.get_status()
    status.pending_count = 1
    await queue.dequeue()
    await queue.nack(message.message_id, error="test")
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 1


@pytest.mark.asyncio
async def test_ack_non_existent_message(queue):
    message = Message(data="non_existent", message_id=uuid.uuid4())
    await queue.ack(message.message_id)
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
    await queue.ack(message.message_id)
    
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
    queue.ack(message.message_id)
    
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
    queue.nack(message.message_id)
    queue.nack(message.message_id)  # Second nack should send to dead letter
    
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
    
    # Enqueue batch of items with different priorities
    await queue.enqueue_batch(["task1"], priority=5)
    await queue.enqueue_batch(["task2"], priority=10)
    await queue.enqueue_batch(["task3"], priority=1)
    
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
    await queue.ack_batch([msg.message_id for msg in messages])
    
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
    await queue.nack_batch([msg.message_id for msg in messages], error="batch error")
    
    status = await queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 3  # All should go to dead letter with max_retries=1


def test_sync_enqueue_batch():
    queue = SyncMemoryQueue()
    
    # Enqueue batch of items with different priorities
    queue.enqueue_batch(["task1"], priority=5)
    queue.enqueue_batch(["task2"], priority=10)
    queue.enqueue_batch(["task3"], priority=1)
    
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
    queue.ack_batch([msg.message_id for msg in messages])
    
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
    queue.nack_batch([msg.message_id for msg in messages], error="batch error")
    
    status = queue.get_status()
    assert status.processing_count == 0
    assert status.dead_letter_count == 3  # All should go to dead letter with max_retries=1


# Sync Key-based queue tests
def test_sync_enqueue_with_key():
    queue = SyncMemoryQueue()
    
    # Enqueue messages with different keys
    queue.enqueue("user_task", priority=1, key="user:123")
    queue.enqueue("admin_task", priority=2, key="admin:456")
    queue.enqueue("general_task", priority=3)  # No key
    
    status = queue.get_status()
    assert status.pending_count == 3


def test_sync_dequeue_by_key():
    queue = SyncMemoryQueue()
    
    # Enqueue messages with different keys
    queue.enqueue("user_task_1", priority=1, key="user:123")
    queue.enqueue("user_task_2", priority=2, key="user:456")
    queue.enqueue("admin_task", priority=3, key="admin:789")
    queue.enqueue("general_task", priority=4)
    
    # Dequeue specific user task
    message = queue.dequeue(key="user:123")
    assert message is not None
    assert message.data == "user_task_1"
    assert message.key == "user:123"
    
    # Dequeue non-existent key
    message = queue.dequeue(key="nonexistent")
    assert message is None
    
    status = queue.get_status()
    assert status.pending_count == 3


def test_sync_dequeue_by_key_prefix():
    queue = SyncMemoryQueue()
    
    # Enqueue messages with different keys
    queue.enqueue("user_task_1", priority=1, key="user:123")
    queue.enqueue("user_task_2", priority=3, key="user:456") 
    queue.enqueue("admin_task", priority=2, key="admin:789")
    queue.enqueue("general_task", priority=4)
    
    # Dequeue by prefix (should get highest priority user task)
    message = queue.dequeue(key_prefix="user:")
    assert message is not None
    assert message.data == "user_task_2"  # priority 3, highest among user tasks
    assert message.key == "user:456"
    
    # Dequeue by admin prefix
    message = queue.dequeue(key_prefix="admin:")
    assert message is not None
    assert message.data == "admin_task"
    assert message.key == "admin:789"
    
    status = queue.get_status()
    assert status.pending_count == 2


def test_sync_dequeue_batch_with_key():
    queue = SyncMemoryQueue()
    
    # Enqueue messages
    queue.enqueue("user_task_1", priority=1, key="user:123")
    queue.enqueue("user_task_2", priority=2, key="user:123")
    queue.enqueue("admin_task", priority=3, key="admin:456")
    
    # Dequeue batch for specific key
    messages = queue.dequeue_batch(limit=3, key="user:123")
    assert len(messages) == 2
    assert messages[0].data == "user_task_2"  # Higher priority first
    assert messages[1].data == "user_task_1"
    
    status = queue.get_status()
    assert status.pending_count == 1
    assert status.processing_count == 2


def test_sync_dequeue_batch_with_key_prefix():
    queue = SyncMemoryQueue()
    
    # Enqueue messages
    queue.enqueue("user_task_1", priority=1, key="user:123")
    queue.enqueue("user_task_2", priority=3, key="user:456")
    queue.enqueue("user_task_3", priority=2, key="user:789")
    queue.enqueue("admin_task", priority=4, key="admin:111")
    
    # Dequeue batch by prefix
    messages = queue.dequeue_batch(limit=5, key_prefix="user:")
    assert len(messages) == 3
    # Should be in priority order
    assert messages[0].data == "user_task_2"  # priority 3
    assert messages[1].data == "user_task_3"  # priority 2
    assert messages[2].data == "user_task_1"  # priority 1
    
    status = queue.get_status()
    assert status.pending_count == 1  # admin task remains
    assert status.processing_count == 3


def test_sync_enqueue_batch_with_key():
    queue = SyncMemoryQueue()
    
    # Enqueue batches with different keys and priorities
    queue.enqueue_batch(["user_task_1"], priority=1, key="user:123")
    queue.enqueue_batch(["admin_task"], priority=2, key="admin:456")
    queue.enqueue_batch(["general_task"], priority=3, key=None)
    
    status = queue.get_status()
    assert status.pending_count == 3
    
    # Test that keys are preserved
    user_msg = queue.dequeue(key="user:123")
    assert user_msg is not None
    assert user_msg.data == "user_task_1"
    assert user_msg.key == "user:123"
    
    admin_msg = queue.dequeue(key="admin:456")
    assert admin_msg is not None
    assert admin_msg.data == "admin_task"
    assert admin_msg.key == "admin:456"
    
    general_msg = queue.dequeue()
    assert general_msg is not None
    assert general_msg.data == "general_task"
    assert general_msg.key is None


def test_sync_get_status_by_prefix():
    queue = SyncMemoryQueue()
    
    # Enqueue messages
    queue.enqueue("user_task_1", key="user:123")
    queue.enqueue("user_task_2", key="user:456")
    queue.enqueue("admin_task", key="admin:789")
    queue.enqueue("general_task")
    
    # Get status for user tasks only
    user_status = queue.get_status(key_prefix="user:")
    assert user_status.pending_count == 2
    assert user_status.processing_count == 0
    
    # Get status for admin tasks
    admin_status = queue.get_status(key_prefix="admin:")
    assert admin_status.pending_count == 1
    
    # Get total status
    total_status = queue.get_status()
    assert total_status.pending_count == 4
    
    # Dequeue a user task and check status again
    msg = queue.dequeue(key="user:123")
    assert msg is not None
    queue.ack(msg.message_id)
    
    user_status = queue.get_status(key_prefix="user:")
    assert user_status.pending_count == 1
    assert user_status.success_count == 1


def test_sync_mixed_key_and_general_dequeue():
    queue = SyncMemoryQueue()
    
    # Enqueue mixed messages
    queue.enqueue("general_high", priority=10)
    queue.enqueue("user_medium", priority=5, key="user:123")
    queue.enqueue("admin_low", priority=1, key="admin:456")
    
    # Dequeue without key should get highest priority across all queues
    message = queue.dequeue()
    assert message is not None
    assert message.data == "general_high"  # Highest priority
    assert message.key is None
    
    # Next should be user task
    message = queue.dequeue()
    assert message is not None
    assert message.data == "user_medium"
    assert message.key == "user:123"
    
    # Last should be admin task
    message = queue.dequeue()
    assert message is not None
    assert message.data == "admin_low"
    assert message.key == "admin:456"


def test_sync_key_message_timeout_and_requeue():
    queue = SyncMemoryQueue(max_retries=2)
    
    # Enqueue message with key
    queue.enqueue("user_task", priority=1, key="user:123")
    
    # Dequeue and let it timeout
    message = queue.dequeue(visibility_timeout=1, key="user:123")
    assert message is not None
    assert message.key == "user:123"
    
    time.sleep(1.1)  # Let it timeout
    
    # Should be requeued to the same key queue
    requeued_message = queue.dequeue(key="user:123")
    assert requeued_message is not None
    assert requeued_message.data == "user_task"
    assert requeued_message.key == "user:123"
    assert requeued_message.retry_count == 1
    
    queue.ack(requeued_message.message_id)


def test_sync_key_nack_and_requeue():
    queue = SyncMemoryQueue(max_retries=2)
    
    # Enqueue message with key
    queue.enqueue("user_task", priority=1, key="user:123")
    
    # Dequeue and nack
    message = queue.dequeue(key="user:123")
    assert message is not None
    assert message.key == "user:123"
    
    queue.nack(message.message_id, error="processing failed")
    
    # Should be requeued to the same key queue
    requeued_message = queue.dequeue(key="user:123")
    assert requeued_message is not None
    assert requeued_message.data == "user_task"
    assert requeued_message.key == "user:123"
    assert requeued_message.retry_count == 1
    assert requeued_message.error_message == "processing failed"
    
    queue.ack(requeued_message.message_id)


def test_sync_key_dead_letter_with_prefix_status():
    queue = SyncMemoryQueue(max_retries=1)
    
    # Enqueue messages with keys
    queue.enqueue("user_task_1", key="user:123")
    queue.enqueue("user_task_2", key="user:456")
    queue.enqueue("admin_task", key="admin:789")
    
    # Process and nack user tasks (should go to dead letter)
    user_msg1 = queue.dequeue(key="user:123")
    assert user_msg1 is not None
    queue.nack(user_msg1.message_id)
    queue.nack(user_msg1.message_id)  # Second nack sends to dead letter
    
    user_msg2 = queue.dequeue(key="user:456") 
    assert user_msg2 is not None
    queue.nack(user_msg2.message_id)
    queue.nack(user_msg2.message_id)  # Second nack sends to dead letter
    
    # Check prefix-based status
    user_status = queue.get_status(key_prefix="user:")
    assert user_status.pending_count == 0
    assert user_status.processing_count == 0
    assert user_status.dead_letter_count == 2
    
    admin_status = queue.get_status(key_prefix="admin:")
    assert admin_status.pending_count == 1
    assert admin_status.dead_letter_count == 0
    
    total_status = queue.get_status()
    assert total_status.pending_count == 1
    assert total_status.dead_letter_count == 2


def test_sync_key_extend_visibility():
    queue = SyncMemoryQueue(max_retries=2)
    
    # Enqueue message with key
    queue.enqueue("user_task", priority=1, key="user:123")
    
    # Dequeue with short timeout
    message = queue.dequeue(visibility_timeout=1, key="user:123")
    assert message is not None
    assert message.key == "user:123"
    
    time.sleep(0.9)  # Wait most of the timeout
    
    # Extend visibility
    extended = queue.extend_visibility(message.message_id, 2)
    assert extended is True
    
    time.sleep(0.5)  # Should still be in flight due to extension
    
    # Should not be available for dequeue yet
    no_message = queue.dequeue(key="user:123")
    assert no_message is None
    
    time.sleep(2)  # Wait for extended timeout
    
    # Now should be requeued
    requeued_message = queue.dequeue(key="user:123")
    assert requeued_message is not None
    assert requeued_message.data == "user_task"
    assert requeued_message.key == "user:123"
    assert requeued_message.retry_count == 1
    
    queue.ack(requeued_message.message_id)


def test_sync_empty_key_queue_operations():
    queue = SyncMemoryQueue()
    
    # Try to dequeue from non-existent key
    message = queue.dequeue(key="nonexistent")
    assert message is None
    
    # Try to dequeue batch from non-existent key
    messages = queue.dequeue_batch(limit=5, key="nonexistent")
    assert len(messages) == 0
    
    # Try to dequeue by non-existent prefix
    message = queue.dequeue(key_prefix="nonexistent:")
    assert message is None
    
    messages = queue.dequeue_batch(limit=5, key_prefix="nonexistent:")
    assert len(messages) == 0
    
    # Status should be empty
    status = queue.get_status(key_prefix="nonexistent:")
    assert status.pending_count == 0
    assert status.processing_count == 0
    assert status.success_count == 0
    assert status.dead_letter_count == 0


def test_sync_key_queue_capacity_limit():
    queue = SyncMemoryQueue(max_size=3)
    
    # Fill queue with different keys
    queue.enqueue("user_1", key="user:123")
    queue.enqueue("admin_1", key="admin:456") 
    queue.enqueue("general_1")  # No key
    
    assert queue._is_full()
    
    # Test blocking with threading for key-specific enqueue
    enqueue_completed = threading.Event()
    enqueue_started = threading.Event()
    
    def enqueue_task():
        enqueue_started.set()
        queue.enqueue("user_2", key="user:123")  # This should block
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
    message = queue.dequeue(key="user:123")
    assert message is not None
    queue.ack(message.message_id)
    
    # Now the enqueue should complete
    enqueue_completed.wait(timeout=1.0)
    assert enqueue_completed.is_set()
    
    thread.join()
    
    status = queue.get_status()
    assert status.pending_count == 3  # admin_1, general_1, user_2


# Key-based queue tests
@pytest.mark.asyncio
async def test_async_enqueue_with_key():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages with different keys
    await queue.enqueue("user_task", priority=1, key="user:123")
    await queue.enqueue("admin_task", priority=2, key="admin:456")
    await queue.enqueue("general_task", priority=3)  # No key
    
    status = await queue.get_status()
    assert status.pending_count == 3


@pytest.mark.asyncio
async def test_async_dequeue_by_key():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages with different keys
    await queue.enqueue("user_task_1", priority=1, key="user:123")
    await queue.enqueue("user_task_2", priority=2, key="user:456")
    await queue.enqueue("admin_task", priority=3, key="admin:789")
    await queue.enqueue("general_task", priority=4)
    
    # Dequeue specific user task
    message = await queue.dequeue(key="user:123")
    assert message is not None
    assert message.data == "user_task_1"
    assert message.key == "user:123"
    
    # Dequeue non-existent key
    message = await queue.dequeue(key="nonexistent")
    assert message is None
    
    status = await queue.get_status()
    assert status.pending_count == 3


@pytest.mark.asyncio
async def test_async_dequeue_by_key_prefix():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages with different keys
    await queue.enqueue("user_task_1", priority=1, key="user:123")
    await queue.enqueue("user_task_2", priority=3, key="user:456") 
    await queue.enqueue("admin_task", priority=2, key="admin:789")
    await queue.enqueue("general_task", priority=4)
    
    # Dequeue by prefix (should get highest priority user task)
    message = await queue.dequeue(key_prefix="user:")
    assert message is not None
    assert message.data == "user_task_2"  # priority 3, highest among user tasks
    assert message.key == "user:456"
    
    # Dequeue by admin prefix
    message = await queue.dequeue(key_prefix="admin:")
    assert message is not None
    assert message.data == "admin_task"
    assert message.key == "admin:789"
    
    status = await queue.get_status()
    assert status.pending_count == 2


@pytest.mark.asyncio
async def test_async_dequeue_batch_with_key():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages
    await queue.enqueue("user_task_1", priority=1, key="user:123")
    await queue.enqueue("user_task_2", priority=2, key="user:123")
    await queue.enqueue("admin_task", priority=3, key="admin:456")
    
    # Dequeue batch for specific key
    messages = await queue.dequeue_batch(limit=3, key="user:123")
    assert len(messages) == 2
    assert messages[0].data == "user_task_2"  # Higher priority first
    assert messages[1].data == "user_task_1"
    
    status = await queue.get_status()
    assert status.pending_count == 1
    assert status.processing_count == 2


@pytest.mark.asyncio
async def test_async_dequeue_batch_with_key_prefix():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages
    await queue.enqueue("user_task_1", priority=1, key="user:123")
    await queue.enqueue("user_task_2", priority=3, key="user:456")
    await queue.enqueue("user_task_3", priority=2, key="user:789")
    await queue.enqueue("admin_task", priority=4, key="admin:111")
    
    # Dequeue batch by prefix
    messages = await queue.dequeue_batch(limit=5, key_prefix="user:")
    assert len(messages) == 3
    # Should be in priority order
    assert messages[0].data == "user_task_2"  # priority 3
    assert messages[1].data == "user_task_3"  # priority 2
    assert messages[2].data == "user_task_1"  # priority 1
    
    status = await queue.get_status()
    assert status.pending_count == 1  # admin task remains
    assert status.processing_count == 3


@pytest.mark.asyncio
async def test_async_enqueue_batch_with_key():
    queue = AsyncMemoryQueue()
    
    # Enqueue batches with different keys and priorities
    await queue.enqueue_batch(["user_task_1"], priority=1, key="user:123")
    await queue.enqueue_batch(["admin_task"], priority=2, key="admin:456")
    await queue.enqueue_batch(["general_task"], priority=3, key=None)
    
    status = await queue.get_status()
    assert status.pending_count == 3
    
    # Test that keys are preserved
    user_msg = await queue.dequeue(key="user:123")
    assert user_msg is not None
    assert user_msg.data == "user_task_1"
    assert user_msg.key == "user:123"
    
    admin_msg = await queue.dequeue(key="admin:456")
    assert admin_msg is not None
    assert admin_msg.data == "admin_task"
    assert admin_msg.key == "admin:456"
    
    general_msg = await queue.dequeue()
    assert general_msg is not None
    assert general_msg.data == "general_task"
    assert general_msg.key is None


@pytest.mark.asyncio
async def test_async_get_status_by_prefix():
    queue = AsyncMemoryQueue()
    
    # Enqueue messages
    await queue.enqueue("user_task_1", key="user:123")
    await queue.enqueue("user_task_2", key="user:456")
    await queue.enqueue("admin_task", key="admin:789")
    await queue.enqueue("general_task")
    
    # Get status for user tasks only
    user_status = await queue.get_status(key_prefix="user:")
    assert user_status.pending_count == 2
    assert user_status.processing_count == 0
    
    # Get status for admin tasks
    admin_status = await queue.get_status(key_prefix="admin:")
    assert admin_status.pending_count == 1
    
    # Get total status
    total_status = await queue.get_status()
    assert total_status.pending_count == 4
    
    # Dequeue a user task and check status again
    msg = await queue.dequeue(key="user:123")
    assert msg is not None
    await queue.ack(msg.message_id)
    
    user_status = await queue.get_status(key_prefix="user:")
    assert user_status.pending_count == 1
    assert user_status.success_count == 1


@pytest.mark.asyncio
async def test_async_mixed_key_and_general_dequeue():
    queue = AsyncMemoryQueue()
    
    # Enqueue mixed messages
    await queue.enqueue("general_high", priority=10)
    await queue.enqueue("user_medium", priority=5, key="user:123")
    await queue.enqueue("admin_low", priority=1, key="admin:456")
    
    # Dequeue without key should get highest priority across all queues
    message = await queue.dequeue()
    assert message is not None
    assert message.data == "general_high"  # Highest priority
    assert message.key is None
    
    # Next should be user task
    message = await queue.dequeue()
    assert message is not None
    assert message.data == "user_medium"
    assert message.key == "user:123"
    
    # Last should be admin task
    message = await queue.dequeue()
    assert message is not None
    assert message.data == "admin_low"
    assert message.key == "admin:456"


@pytest.mark.asyncio
async def test_async_key_message_timeout_and_requeue():
    queue = AsyncMemoryQueue(max_retries=2)
    
    # Enqueue message with key
    await queue.enqueue("user_task", priority=1, key="user:123")
    
    # Dequeue and let it timeout
    message = await queue.dequeue(visibility_timeout=1, key="user:123")
    assert message is not None
    assert message.key == "user:123"
    
    await asyncio.sleep(1.1)  # Let it timeout
    
    # Should be requeued to the same key queue
    requeued_message = await queue.dequeue(key="user:123")
    assert requeued_message is not None
    assert requeued_message.data == "user_task"
    assert requeued_message.key == "user:123"
    assert requeued_message.retry_count == 1
    
    await queue.ack(requeued_message.message_id)
