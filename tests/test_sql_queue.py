import asyncio
import tempfile
from pathlib import Path

import pytest

from inch.aio.queue.sql_queue import AsyncSQLQueue
from inch.types import MessageStatus
from inch.queue.sql_queue import SyncSQLQueue


def test_sync_sql_queue_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        queue = SyncSQLQueue(connection_string, "test_queue", max_retries=3)

        # Test enqueue and dequeue
        queue.enqueue("test_message", priority=1)
        message = queue.dequeue()

        assert message is not None
        assert message.data == "test_message"
        assert message.priority == 1
        assert message.status == MessageStatus.PROCESSING

        # Test ack
        queue.ack(message.message_id)

        # Test status
        status = queue.get_status()
        assert status.pending_count == 0
        assert status.processing_count == 0
        assert status.success_count == 1
        assert status.dead_letter_count == 0


def test_sync_sql_queue_with_keys():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        queue = SyncSQLQueue(connection_string, "test_queue", max_retries=3)

        # Test enqueue with keys
        queue.enqueue("message1", priority=1, key="key1")
        queue.enqueue("message2", priority=2, key="key2")
        queue.enqueue("message3", priority=1, key="key1_sub")

        # Test dequeue with specific key
        message = queue.dequeue(key="key1")
        assert message is not None
        assert message.data == "message1"
        assert message.key == "key1"

        # Test dequeue with key prefix
        message = queue.dequeue(key_prefix="key1")
        assert message is not None
        assert message.data == "message3"
        assert message.key == "key1_sub"

        # Clean up
        queue.clear()


def test_sync_sql_queue_priority():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        queue = SyncSQLQueue(connection_string, "test_queue")

        # Enqueue messages with different priorities
        queue.enqueue("low_priority", priority=1)
        queue.enqueue("high_priority", priority=10)
        queue.enqueue("medium_priority", priority=5)

        # Should dequeue in priority order (highest first)
        message1 = queue.dequeue()
        assert message1 and message1.data == "high_priority"

        message2 = queue.dequeue()
        assert message2 and message2.data == "medium_priority"

        message3 = queue.dequeue()
        assert message3 and message3.data == "low_priority"

        queue.clear()


def test_sync_sql_queue_batch_operations():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        queue = SyncSQLQueue(connection_string, "test_queue")

        # Test batch enqueue
        items = [("msg1", 1), ("msg2", 2), ("msg3", 3)]
        queue.enqueue_batch(items)

        # Test batch dequeue
        messages = queue.dequeue_batch(limit=2)
        assert len(messages) == 2
        assert messages[0].data == "msg3"  # Highest priority first
        assert messages[1].data == "msg2"

        # Test batch ack
        message_ids = [msg.message_id for msg in messages]
        queue.ack_batch(message_ids)

        status = queue.get_status()
        assert status.pending_count == 1
        assert status.success_count == 2

        queue.clear()


def test_sync_sql_queue_nack_and_retry():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        queue = SyncSQLQueue(connection_string, "test_queue", max_retries=2)

        queue.enqueue("test_message")
        message = queue.dequeue()
        assert message is not None

        # First nack
        queue.nack(message.message_id, "error1")
        status = queue.get_status()
        assert status.pending_count == 1
        assert status.dead_letter_count == 0

        # Second nack (should go to dead letter)
        message = queue.dequeue()
        assert message is not None
        queue.nack(message.message_id, "error2")

        status = queue.get_status()
        assert status.pending_count == 0
        assert status.dead_letter_count == 1

        # Check dead letter messages
        dead_messages = queue.get_dead_letter_messages()
        assert len(dead_messages) == 1
        assert dead_messages[0].data == "test_message"
        assert dead_messages[0].retry_count == 2

        queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue", max_retries=3)

        # Test enqueue and dequeue
        await queue.enqueue("test_message", priority=1)
        message = await queue.dequeue()

        assert message is not None
        assert message.data == "test_message"
        assert message.priority == 1
        assert message.status == MessageStatus.PROCESSING

        # Test ack
        await queue.ack(message.message_id)

        # Test status
        status = await queue.get_status()
        assert status.pending_count == 0
        assert status.processing_count == 0
        assert status.success_count == 1
        assert status.dead_letter_count == 0

        await queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_with_keys():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue", max_retries=3)

        # Test enqueue with keys
        await queue.enqueue("message1", priority=1, key="key1")
        await queue.enqueue("message2", priority=2, key="key2")
        await queue.enqueue("message3", priority=1, key="key1_sub")

        # Test dequeue with specific key
        message = await queue.dequeue(key="key1")
        assert message is not None
        assert message.data == "message1"
        assert message.key == "key1"

        # Test dequeue with key prefix
        message = await queue.dequeue(key_prefix="key1")
        assert message is not None
        assert message.data == "message3"
        assert message.key == "key1_sub"

        await queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_priority():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue")

        # Enqueue messages with different priorities
        await queue.enqueue("low_priority", priority=1)
        await queue.enqueue("high_priority", priority=10)
        await queue.enqueue("medium_priority", priority=5)

        # Should dequeue in priority order (highest first)
        message1 = await queue.dequeue()
        assert message1 is not None
        assert message1.data == "high_priority"

        message2 = await queue.dequeue()
        assert message2 is not None
        assert message2.data == "medium_priority"

        message3 = await queue.dequeue()
        assert message3 is not None
        assert message3.data == "low_priority"

        await queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_batch_operations():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue")

        # Test batch enqueue
        items = [("msg1", 1), ("msg2", 2), ("msg3", 3)]
        await queue.enqueue_batch(items)

        # Test batch dequeue
        messages = await queue.dequeue_batch(limit=2)
        assert len(messages) == 2
        assert messages[0].data == "msg3"  # Highest priority first
        assert messages[1].data == "msg2"

        # Test batch ack
        message_ids = [msg.message_id for msg in messages]
        await queue.ack_batch(message_ids)

        status = await queue.get_status()
        assert status.pending_count == 1
        assert status.success_count == 2

        await queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_nack_and_retry():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue", max_retries=2)

        await queue.enqueue("test_message")
        message = await queue.dequeue()
        assert message is not None

        # First nack
        await queue.nack(message.message_id, "error1")
        status = await queue.get_status()
        assert status.pending_count == 1
        assert status.dead_letter_count == 0

        # Second nack (should go to dead letter)
        message = await queue.dequeue()
        assert message is not None
        await queue.nack(message.message_id, "error2")

        status = await queue.get_status()
        assert status.pending_count == 0
        assert status.dead_letter_count == 1

        # Check dead letter messages
        dead_messages = await queue.get_dead_letter_messages()
        assert len(dead_messages) == 1
        assert dead_messages[0].data == "test_message"
        assert dead_messages[0].retry_count == 2

        await queue.clear()


@pytest.mark.asyncio
async def test_async_sql_queue_visibility_timeout():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        queue = AsyncSQLQueue(connection_string, "test_queue", max_retries=3)

        await queue.enqueue("test_message")
        await queue.dequeue(visibility_timeout=1)

        # Wait for timeout to expire
        await asyncio.sleep(1.1)

        # Message should be back in queue
        status = await queue.get_status()
        assert status.pending_count == 1
        assert status.processing_count == 0

        await queue.clear()
