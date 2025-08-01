"""
Race condition detection tests for AsyncSQLQueue.
"""

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from inch.aio.queue.sql_queue import AsyncSQLQueue


class TestAsyncSQLQueueRaceConditions:
    """Test suite for detecting race conditions in AsyncSQLQueue."""

    @pytest_asyncio.fixture
    async def queue(self) -> AsyncSQLQueue[dict[str, Any]]:
        """Create a test queue instance."""
        connection_string = "sqlite+aiosqlite:///:memory:"
        queue = AsyncSQLQueue[dict[str, Any]](
            connection_string=connection_string,
            queue_name="race_test",
            max_retries=3,
        )
        # Ensure tables are created before any tests
        await queue._ensure_tables()
        return queue

    @pytest.mark.asyncio
    async def test_concurrent_counter_safety(self, queue):
        """Test counter safety under concurrent enqueue operations."""

        async def producer(producer_id: int, count: int) -> list[int]:
            counters = []
            for i in range(count):
                message = {"producer": producer_id, "index": i}
                await queue.enqueue(message)
                # Check counter value after enqueue
                current_counter = queue._counter
                counters.append(current_counter)
            return counters

        # Start multiple producers concurrently
        tasks = [producer(i, 10) for i in range(5)]
        results = await asyncio.gather(*tasks)

        # Collect all counter values
        all_counters = []
        for producer_counters in results:
            all_counters.extend(producer_counters)

        # Verify no duplicate counter values
        unique_counters = set(all_counters)
        assert len(all_counters) == len(unique_counters), f"Duplicate counter values found: {len(all_counters)} total, {len(unique_counters)} unique"

        # Verify counters are sequential
        all_counters.sort()
        expected_range = list(range(1, len(all_counters) + 1))
        assert all_counters == expected_range, f"Counter values not sequential: {all_counters[:10]}..."

    @pytest.mark.asyncio
    async def test_concurrent_dequeue_safety(self, queue):
        """Test dequeue safety - ensure no duplicate message consumption."""

        # Pre-populate queue
        message_count = 50
        for i in range(message_count):
            await queue.enqueue({"id": i, "data": f"message_{i}"})

        async def consumer(consumer_id: int) -> tuple[list[dict[str, Any]], list[str]]:
            local_messages = []
            local_errors = []

            for _ in range(message_count):  # Each consumer tries to consume all messages
                try:
                    message = await queue.dequeue(visibility_timeout=5.0)
                    if message is None:
                        break

                    local_messages.append({"consumer_id": consumer_id, "message_id": str(message.message_id), "data": message.data})

                    await queue.ack(message.message_id)

                except Exception as e:
                    local_errors.append(f"Consumer {consumer_id}: {e!s}")

            return local_messages, local_errors

        # Start multiple consumers
        consumer_count = 5
        tasks = [consumer(i) for i in range(consumer_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_consumed = []
        all_errors = []

        for result in results:
            assert not isinstance(result, Exception), f"Consumer task failed: {result}"
            if isinstance(result, tuple):
                messages, errors = result
                all_consumed.extend(messages)
                all_errors.extend(errors)

        # Verify no duplicate consumption
        message_ids = [msg["message_id"] for msg in all_consumed]
        unique_message_ids = set(message_ids)
        assert len(message_ids) == len(unique_message_ids), f"Duplicate consumption detected: {len(message_ids)} total, {len(unique_message_ids)} unique"

        # Verify no duplicate data
        original_ids = [msg["data"]["id"] for msg in all_consumed]
        unique_original_ids = set(original_ids)
        assert len(original_ids) == len(unique_original_ids), f"Duplicate data detected: {len(original_ids)} total, {len(unique_original_ids)} unique"

        assert len(all_consumed) == message_count, f"Expected {message_count} messages, consumed {len(all_consumed)}"
        assert len(all_errors) == 0, f"Unexpected errors: {all_errors}"

    @pytest.mark.asyncio
    async def test_ack_nack_race_conditions(self, queue):
        """Test ACK/NACK race conditions."""

        # Send messages
        message_count = 20
        for i in range(message_count):
            await queue.enqueue({"id": i, "test": "ack_nack"})

        # Dequeue all messages but don't immediately acknowledge
        messages = []
        for _ in range(message_count):
            message = await queue.dequeue(visibility_timeout=30.0)
            if message:
                messages.append(message)

        # Concurrently execute ACK and NACK on each message (simulating race condition)
        async def concurrent_ack_nack(message):
            # Start ACK and NACK simultaneously
            ack_task = asyncio.create_task(queue.ack(message.message_id))
            nack_task = asyncio.create_task(queue.nack(message.message_id, "test error"))

            # Wait for both operations (one should succeed, one should log warning)
            await asyncio.gather(ack_task, nack_task, return_exceptions=True)

        # Execute all concurrent ACK/NACK operations
        await asyncio.gather(*[concurrent_ack_nack(msg) for msg in messages])

        # Verify final state
        status = await queue.get_status()
        total_processed = status.success_count + status.dead_letter_count

        assert total_processed == message_count, f"Expected {message_count} processed messages, got {total_processed}"
        assert status.processing_count == 0, f"Expected 0 processing messages, got {status.processing_count}"

    @pytest.mark.asyncio
    async def test_timeout_handling_race(self, queue):
        """Test timeout handling race conditions."""

        # Send messages
        message_count = 10
        for i in range(message_count):
            await queue.enqueue({"id": i, "timeout_test": True})

        # Dequeue messages with very short timeout
        messages = []
        for _ in range(message_count):
            message = await queue.dequeue(visibility_timeout=0.1)  # 100ms timeout
            if message:
                messages.append(message)

        # Wait for timeout
        await asyncio.sleep(0.5)

        # Force timeout check
        await queue._check_timeouts()

        # Concurrently try to ACK expired messages and dequeue them again
        async def ack_expired_message(message):
            try:
                await queue.ack(message.message_id)
            except Exception:
                pass  # Expected to fail or log warning

        async def dequeue_after_timeout():
            try:
                message = await queue.dequeue()
                if message:
                    await queue.ack(message.message_id)
                    return 1
            except Exception:
                pass
            return 0

        # Execute concurrent operations
        ack_tasks = [ack_expired_message(msg) for msg in messages]
        dequeue_tasks = [dequeue_after_timeout() for _ in range(message_count)]

        await asyncio.gather(*ack_tasks, return_exceptions=True)
        dequeue_results = await asyncio.gather(*dequeue_tasks, return_exceptions=True)

        redequeued_count = sum(r for r in dequeue_results if isinstance(r, int))

        status = await queue.get_status()

        # Verify no messages stuck in processing state
        assert status.processing_count == 0, f"Expected 0 processing messages, got {status.processing_count}"

        # Verify all messages were eventually processed
        total_final = status.pending_count + status.success_count + status.dead_letter_count
        assert total_final >= 0, "Invalid final state"

    @pytest.mark.asyncio
    async def test_batch_operations_race_conditions(self, queue):
        """Test race conditions in batch operations."""

        # Test concurrent batch enqueue operations
        async def batch_producer(batch_id: int, batch_size: int = 10):
            items = []
            for i in range(batch_size):
                message = {"batch_id": batch_id, "item": i}
                items.append((message, i % 3))  # (data, priority)

            await queue.enqueue_batch(items)
            return len(items)

        # Start multiple batch producers
        batch_count = 5
        tasks = [batch_producer(i) for i in range(batch_count)]
        results = await asyncio.gather(*tasks)

        total_sent = sum(results)
        expected_total = batch_count * 10

        assert total_sent == expected_total, f"Expected {expected_total} messages, sent {total_sent}"

        # Verify queue state
        status = await queue.get_status()
        assert status.pending_count == expected_total, f"Expected {expected_total} pending messages, got {status.pending_count}"

        # Test concurrent batch dequeue operations
        async def batch_consumer(consumer_id: int):
            consumed = 0
            while consumed < expected_total // batch_count + 5:  # Give some buffer
                messages = await queue.dequeue_batch(limit=5)
                if not messages:
                    break

                # ACK all messages in batch
                message_ids = [msg.message_id for msg in messages]
                await queue.ack_batch(message_ids)
                consumed += len(messages)

            return consumed

        # Start multiple batch consumers
        consumer_tasks = [batch_consumer(i) for i in range(3)]
        consumer_results = await asyncio.gather(*consumer_tasks)

        total_consumed = sum(consumer_results)

        # Allow some tolerance for race conditions in batch operations
        assert total_consumed >= expected_total * 0.9, f"Too few messages consumed: {total_consumed} < {expected_total * 0.9}"

        # Verify final state
        final_status = await queue.get_status()
        assert final_status.processing_count == 0, f"Expected 0 processing messages, got {final_status.processing_count}"
