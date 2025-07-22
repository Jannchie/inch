import asyncio
import time

import pytest
import pytest_asyncio
import redis
import redis.asyncio as aioredis

from inch.aio.queue.redis_queue import AsyncRedisQueue
from inch.queue.base import MessageStatus
from inch.queue.redis_queue import SyncRedisQueue


@pytest.fixture
def redis_client():
    """Create a synchronous Redis client for testing."""
    client = redis.Redis(host="192.168.31.59", port=6379, db=15, decode_responses=True)
    # Clear test database before each test
    client.flushdb()
    yield client
    # Clean up after test
    client.flushdb()
    client.close()


@pytest_asyncio.fixture
async def async_redis_client():
    """Create an asynchronous Redis client for testing."""
    client = aioredis.Redis(host="192.168.31.59", port=6379, db=15, decode_responses=True)
    # Clear test database before each test
    await client.flushdb()
    yield client
    # Clean up after test
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def sync_queue(redis_client):
    """Create a synchronous Redis queue for testing."""
    return SyncRedisQueue(redis_client=redis_client, queue_name="test_queue", max_retries=3)


@pytest_asyncio.fixture
async def async_queue(async_redis_client):
    """Create an asynchronous Redis queue for testing."""
    return AsyncRedisQueue(redis_client=async_redis_client, queue_name="test_queue", max_retries=3)


class TestSyncRedisQueue:
    def test_enqueue_dequeue_ack(self, sync_queue):
        sync_queue.enqueue("task1")
        message = sync_queue.dequeue(visibility_timeout=1)
        assert message is not None
        assert message.data == "task1"
        assert message.status == MessageStatus.PROCESSING

        status = sync_queue.get_status()
        assert status.pending_count == 0
        assert status.processing_count == 1

        sync_queue.ack(message.message_id)
        status = sync_queue.get_status()
        assert status.processing_count == 0
        assert status.success_count == 1

    def test_enqueue_dequeue_nack(self, sync_queue):
        sync_queue.enqueue("task2")
        message = sync_queue.dequeue(visibility_timeout=1)
        assert message is not None
        assert message.data == "task2"

        sync_queue.nack(message.message_id, error="failed")
        status = sync_queue.get_status()
        assert status.processing_count == 0
        assert status.pending_count == 1  # Should be 1 because max_retries is 3
        assert status.dead_letter_count == 0
        dead_letters = sync_queue.get_dead_letter_messages()
        assert len(dead_letters) == 0

    def test_priority_queue(self, sync_queue):
        sync_queue.enqueue("low", priority=1)
        sync_queue.enqueue("high", priority=10)
        sync_queue.enqueue("medium", priority=5)

        # Higher priority should come first
        message1 = sync_queue.dequeue()
        assert message1.data == "high"
        assert message1.priority == 10

        message2 = sync_queue.dequeue()
        assert message2.data == "medium"
        assert message2.priority == 5

        message3 = sync_queue.dequeue()
        assert message3.data == "low"
        assert message3.priority == 1

    def test_key_partitioning(self, sync_queue):
        sync_queue.enqueue("user1_task", key="user:1")
        sync_queue.enqueue("user2_task", key="user:2")
        sync_queue.enqueue("general_task")

        # Dequeue from specific key
        message = sync_queue.dequeue(key="user:1")
        assert message.data == "user1_task"
        assert message.key == "user:1"

        # Dequeue from key prefix
        message = sync_queue.dequeue(key_prefix="user:")
        assert message.data == "user2_task"
        assert message.key == "user:2"

        # Dequeue general
        message = sync_queue.dequeue()
        assert message.data == "general_task"
        assert message.key is None

    def test_batch_operations(self, sync_queue):
        # Test batch enqueue
        items = [("task1", 1), ("task2", 2), ("task3", 3)]
        sync_queue.enqueue_batch(items)

        # Test batch dequeue
        messages = sync_queue.dequeue_batch(limit=2)
        assert len(messages) == 2
        assert messages[0].data == "task3"  # Highest priority first
        assert messages[1].data == "task2"

        # Test batch ack
        message_ids = [msg.message_id for msg in messages]
        sync_queue.ack_batch(message_ids)

        status = sync_queue.get_status()
        assert status.processing_count == 0
        assert status.success_count == 2
        assert status.pending_count == 1

    def test_batch_with_keys(self, sync_queue):
        items = [("task1", 1, "user:1"), ("task2", 2, "user:2"), ("task3", 3, None)]
        sync_queue.enqueue_batch_with_keys(items)

        status = sync_queue.get_status()
        assert status.pending_count == 3

        # Dequeue with key prefix
        messages = sync_queue.dequeue_batch(limit=2, key_prefix="user:")
        assert len(messages) == 2
        assert all(msg.key.startswith("user:") for msg in messages)

    def test_visibility_timeout(self, sync_queue):
        sync_queue.enqueue("timeout_test")
        message = sync_queue.dequeue(visibility_timeout=0.1)
        assert message is not None

        # Wait for timeout
        time.sleep(0.2)

        # Check that message is back in queue due to timeout
        status = sync_queue.get_status()
        assert status.pending_count == 1
        assert status.processing_count == 0

    def test_extend_visibility(self, sync_queue):
        sync_queue.enqueue("extend_test")
        message = sync_queue.dequeue(visibility_timeout=0.1)
        assert message is not None

        # Extend visibility
        result = sync_queue.extend_visibility(message.message_id, 1.0)
        assert result is True

        # Message should still be processing after original timeout
        time.sleep(0.2)
        status = sync_queue.get_status()
        assert status.processing_count == 1

    def test_clear(self, sync_queue):
        sync_queue.enqueue("task1")
        sync_queue.enqueue("task2", key="user:1")
        
        status = sync_queue.get_status()
        assert status.pending_count == 2

        sync_queue.clear()
        status = sync_queue.get_status()
        assert status.pending_count == 0


@pytest.mark.asyncio
class TestAsyncRedisQueue:
    async def test_enqueue_dequeue_ack(self, async_queue):
        await async_queue.enqueue("task1")
        message = await async_queue.dequeue(visibility_timeout=1)
        assert message is not None
        assert message.data == "task1"
        assert message.status == MessageStatus.PROCESSING

        status = await async_queue.get_status()
        assert status.pending_count == 0
        assert status.processing_count == 1

        await async_queue.ack(message.message_id)
        status = await async_queue.get_status()
        assert status.processing_count == 0
        assert status.success_count == 1

    async def test_enqueue_dequeue_nack(self, async_queue):
        await async_queue.enqueue("task2")
        message = await async_queue.dequeue(visibility_timeout=1)
        assert message is not None
        assert message.data == "task2"

        await async_queue.nack(message.message_id, error="failed")
        status = await async_queue.get_status()
        assert status.processing_count == 0
        assert status.pending_count == 1  # Should be 1 because max_retries is 3
        assert status.dead_letter_count == 0
        dead_letters = await async_queue.get_dead_letter_messages()
        assert len(dead_letters) == 0

    async def test_priority_queue(self, async_queue):
        await async_queue.enqueue("low", priority=1)
        await async_queue.enqueue("high", priority=10)
        await async_queue.enqueue("medium", priority=5)

        # Higher priority should come first
        message1 = await async_queue.dequeue()
        assert message1.data == "high"
        assert message1.priority == 10

        message2 = await async_queue.dequeue()
        assert message2.data == "medium"
        assert message2.priority == 5

        message3 = await async_queue.dequeue()
        assert message3.data == "low"
        assert message3.priority == 1

    async def test_key_partitioning(self, async_queue):
        await async_queue.enqueue("user1_task", key="user:1")
        await async_queue.enqueue("user2_task", key="user:2")
        await async_queue.enqueue("general_task")

        # Dequeue from specific key
        message = await async_queue.dequeue(key="user:1")
        assert message.data == "user1_task"
        assert message.key == "user:1"

        # Dequeue from key prefix
        message = await async_queue.dequeue(key_prefix="user:")
        assert message.data == "user2_task"
        assert message.key == "user:2"

        # Dequeue general
        message = await async_queue.dequeue()
        assert message.data == "general_task"
        assert message.key is None

    async def test_batch_operations(self, async_queue):
        # Test batch enqueue
        items = [("task1", 1), ("task2", 2), ("task3", 3)]
        await async_queue.enqueue_batch(items)

        # Test batch dequeue
        messages = await async_queue.dequeue_batch(limit=2)
        assert len(messages) == 2
        assert messages[0].data == "task3"  # Highest priority first
        assert messages[1].data == "task2"

        # Test batch ack
        message_ids = [msg.message_id for msg in messages]
        await async_queue.ack_batch(message_ids)

        status = await async_queue.get_status()
        assert status.processing_count == 0
        assert status.success_count == 2
        assert status.pending_count == 1

    async def test_batch_with_keys(self, async_queue):
        items = [("task1", 1, "user:1"), ("task2", 2, "user:2"), ("task3", 3, None)]
        await async_queue.enqueue_batch_with_keys(items)

        status = await async_queue.get_status()
        assert status.pending_count == 3

        # Dequeue with key prefix
        messages = await async_queue.dequeue_batch(limit=2, key_prefix="user:")
        assert len(messages) == 2
        assert all(msg.key.startswith("user:") for msg in messages)

    async def test_visibility_timeout(self, async_queue):
        await async_queue.enqueue("timeout_test")
        message = await async_queue.dequeue(visibility_timeout=0.1)
        assert message is not None

        # Wait for timeout
        await asyncio.sleep(0.2)

        # Check that message is back in queue due to timeout
        status = await async_queue.get_status()
        assert status.pending_count == 1
        assert status.processing_count == 0

    async def test_extend_visibility(self, async_queue):
        await async_queue.enqueue("extend_test")
        message = await async_queue.dequeue(visibility_timeout=0.1)
        assert message is not None

        # Extend visibility
        result = await async_queue.extend_visibility(message.message_id, 1.0)
        assert result is True

        # Message should still be processing after original timeout
        await asyncio.sleep(0.2)
        status = await async_queue.get_status()
        assert status.processing_count == 1

    async def test_clear(self, async_queue):
        await async_queue.enqueue("task1")
        await async_queue.enqueue("task2", key="user:1")
        
        status = await async_queue.get_status()
        assert status.pending_count == 2

        await async_queue.clear()
        status = await async_queue.get_status()
        assert status.pending_count == 0


# Integration tests that require both sync and async clients
@pytest.mark.asyncio
async def test_sync_async_interoperability(redis_client, async_redis_client):
    """Test that sync and async queues can interoperate with the same Redis instance."""
    sync_queue = SyncRedisQueue(redis_client=redis_client, queue_name="interop_test")
    async_queue = AsyncRedisQueue(redis_client=async_redis_client, queue_name="interop_test")

    # Enqueue with sync, dequeue with async
    sync_queue.enqueue("sync_to_async")
    message = await async_queue.dequeue()
    assert message is not None
    assert message.data == "sync_to_async"
    await async_queue.ack(message.message_id)

    # Enqueue with async, dequeue with sync
    await async_queue.enqueue("async_to_sync")
    message = sync_queue.dequeue()
    assert message is not None
    assert message.data == "async_to_sync"
    sync_queue.ack(message.message_id)

    # Both queues should show the same status
    sync_status = sync_queue.get_status()
    async_status = await async_queue.get_status()
    assert sync_status.success_count == async_status.success_count == 2