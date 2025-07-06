import asyncio

import pytest

from inch.queue.base import Message, MessageStatus
from inch.queue.memory_queue import MemoryQueue


@pytest.fixture
def queue():
    return MemoryQueue(max_retries=1)  # Set max_retries to 1 for easier dead-letter testing


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
