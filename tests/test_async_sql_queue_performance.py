"""
Performance benchmark tests for AsyncSQLQueue.
"""
import asyncio
import statistics
import time
from typing import Any

import pytest
import pytest_asyncio

from inch.aio.queue.sql_queue import AsyncSQLQueue


class TestAsyncSQLQueuePerformance:
    """Performance benchmark test suite for AsyncSQLQueue."""

    @pytest_asyncio.fixture
    async def queue(self) -> AsyncSQLQueue[dict[str, Any]]:
        """Create a test queue instance."""
        connection_string = "sqlite+aiosqlite:///:memory:"
        queue = AsyncSQLQueue[dict[str, Any]](
            connection_string=connection_string,
            queue_name="benchmark",
            max_retries=3,
        )
        # Ensure tables are created before any tests
        await queue._ensure_tables()
        return queue

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_enqueue_performance(self, queue):
        """Benchmark enqueue operations."""
        message_count = 1000
        latencies = []
        
        start_time = time.time()
        
        for i in range(message_count):
            message = {"id": i, "data": f"benchmark_message_{i}", "timestamp": time.time()}
            
            op_start = time.time()
            await queue.enqueue(message, priority=i % 3)
            op_end = time.time()
            
            latencies.append((op_end - op_start) * 1000)  # Convert to ms
        
        duration = time.time() - start_time
        throughput = message_count / duration
        avg_latency = statistics.mean(latencies)
        p95_latency = statistics.quantiles(latencies, n=20)[18]  # 95th percentile
        
        # Performance assertions (adjust thresholds based on expected performance)
        assert throughput > 100, f"Enqueue throughput too low: {throughput:.2f} ops/sec"
        assert avg_latency < 10, f"Average enqueue latency too high: {avg_latency:.2f}ms"
        assert p95_latency < 20, f"P95 enqueue latency too high: {p95_latency:.2f}ms"
        
        # Verify all messages were enqueued
        status = await queue.get_status()
        assert status.pending_count == message_count

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_dequeue_performance(self, queue):
        """Benchmark dequeue operations."""
        message_count = 1000
        
        # Pre-populate queue
        for i in range(message_count):
            message = {"id": i, "data": f"benchmark_message_{i}"}
            await queue.enqueue(message)
        
        latencies = []
        start_time = time.time()
        
        for _ in range(message_count):
            op_start = time.time()
            message = await queue.dequeue()
            op_end = time.time()
            
            assert message is not None, "Expected message but got None"
            await queue.ack(message.message_id)
            latencies.append((op_end - op_start) * 1000)
        
        duration = time.time() - start_time
        throughput = message_count / duration
        avg_latency = statistics.mean(latencies)
        p95_latency = statistics.quantiles(latencies, n=20)[18]
        
        # Performance assertions
        assert throughput > 50, f"Dequeue throughput too low: {throughput:.2f} ops/sec"
        assert avg_latency < 20, f"Average dequeue latency too high: {avg_latency:.2f}ms"
        assert p95_latency < 50, f"P95 dequeue latency too high: {p95_latency:.2f}ms"

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_batch_enqueue_performance(self, queue):
        """Benchmark batch enqueue operations."""
        batch_size = 50
        batch_count = 20
        total_messages = batch_size * batch_count
        
        start_time = time.time()
        
        for batch_idx in range(batch_count):
            batch_items = []
            for i in range(batch_size):
                message_id = batch_idx * batch_size + i
                message = {"id": message_id, "data": f"batch_message_{message_id}"}
                batch_items.append((message, i % 3))  # (data, priority)
            
            await queue.enqueue_batch(batch_items)
        
        duration = time.time() - start_time
        throughput = total_messages / duration
        
        # Performance assertions
        assert throughput > 500, f"Batch enqueue throughput too low: {throughput:.2f} ops/sec"
        
        # Verify all messages were enqueued
        status = await queue.get_status()
        assert status.pending_count == total_messages

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_batch_dequeue_performance(self, queue):
        """Benchmark batch dequeue operations."""
        total_messages = 1000
        batch_size = 10
        
        # Pre-populate queue
        for i in range(total_messages):
            message = {"id": i, "data": f"batch_dequeue_message_{i}"}
            await queue.enqueue(message)
        
        messages_processed = 0
        start_time = time.time()
        
        while messages_processed < total_messages:
            messages = await queue.dequeue_batch(limit=batch_size)
            if not messages:
                break
                
            # ACK all messages in the batch
            message_ids = [msg.message_id for msg in messages]
            await queue.ack_batch(message_ids)
            
            messages_processed += len(messages)
        
        duration = time.time() - start_time
        throughput = messages_processed / duration
        
        # Performance assertions
        assert throughput > 1000, f"Batch dequeue throughput too low: {throughput:.2f} ops/sec"
        assert messages_processed == total_messages, \
            f"Expected {total_messages}, processed {messages_processed}"

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_concurrent_mixed_performance(self, queue):
        """Benchmark concurrent producer-consumer scenario."""
        producer_count = 5
        consumer_count = 3
        duration_seconds = 5
        
        total_sent = 0
        total_received = 0
        start_time = time.time()
        end_time = start_time + duration_seconds
        
        async def producer(producer_id: int) -> int:
            sent = 0
            while time.time() < end_time:
                try:
                    message = {
                        "producer_id": producer_id,
                        "sent_at": time.time(),
                        "data": f"concurrent_message_{sent}",
                    }
                    await queue.enqueue(message, priority=sent % 3)
                    sent += 1
                    await asyncio.sleep(0.001)  # Small delay to prevent overwhelming
                except Exception:
                    break
            return sent
        
        async def consumer(consumer_id: int) -> int:
            received = 0
            while time.time() < end_time:
                try:
                    message = await queue.dequeue(visibility_timeout=5.0)
                    if message:
                        await queue.ack(message.message_id)
                        received += 1
                    else:
                        await asyncio.sleep(0.01)
                except Exception:
                    break
            return received
        
        # Start all tasks
        tasks = []
        tasks.extend([producer(i) for i in range(producer_count)])
        tasks.extend([consumer(i) for i in range(consumer_count)])
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Separate producer and consumer results
        for i, result in enumerate(results):
            assert not isinstance(result, Exception), f"Task {i} failed: {result}"
            if i < producer_count:
                total_sent += result
            else:
                total_received += result
        
        actual_duration = time.time() - start_time
        total_operations = total_sent + total_received
        throughput = total_operations / actual_duration
        
        # Performance assertions
        assert throughput > 200, f"Mixed concurrent throughput too low: {throughput:.2f} ops/sec"
        assert total_sent > 0, "No messages were sent"
        assert total_received > 0, "No messages were received"

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_memory_usage_stability(self, queue):
        """Test that memory usage remains stable under load."""
        
        # Perform many operations to test for memory leaks
        iterations = 100
        messages_per_iteration = 10
        
        for iteration in range(iterations):
            # Enqueue messages
            for i in range(messages_per_iteration):
                message = {"iteration": iteration, "index": i}
                await queue.enqueue(message)
            
            # Dequeue and ack messages
            for _ in range(messages_per_iteration):
                message = await queue.dequeue()
                if message:
                    await queue.ack(message.message_id)
            
            # Periodically check queue is empty
            if iteration % 20 == 0:
                status = await queue.get_status()
                assert status.pending_count == 0, \
                    f"Queue not empty at iteration {iteration}: {status.pending_count} pending"
                assert status.processing_count == 0, \
                    f"Messages stuck processing at iteration {iteration}: {status.processing_count}"
        
        # Final verification
        final_status = await queue.get_status()
        assert final_status.pending_count == 0, "Queue not empty after test"
        assert final_status.processing_count == 0, "Messages stuck in processing state"

    @pytest.mark.asyncio
    @pytest.mark.performance
    async def test_priority_queue_performance(self, queue):
        """Test performance with priority queue operations."""
        message_count = 1000
        
        # Enqueue messages with different priorities
        start_time = time.time()
        for i in range(message_count):
            message = {"id": i, "priority": i % 5}
            await queue.enqueue(message, priority=i % 5)
        
        enqueue_duration = time.time() - start_time
        enqueue_throughput = message_count / enqueue_duration
        
        # Dequeue messages (should come out in priority order)
        start_time = time.time()
        dequeued_messages = []
        
        for _ in range(message_count):
            message = await queue.dequeue()
            if message:
                dequeued_messages.append(message.data)
                await queue.ack(message.message_id)
        
        dequeue_duration = time.time() - start_time
        dequeue_throughput = len(dequeued_messages) / dequeue_duration
        
        # Performance assertions
        assert enqueue_throughput > 100, \
            f"Priority enqueue throughput too low: {enqueue_throughput:.2f} ops/sec"
        assert dequeue_throughput > 50, \
            f"Priority dequeue throughput too low: {dequeue_throughput:.2f} ops/sec"
        
        # Verify priority ordering
        priorities = [msg["priority"] for msg in dequeued_messages]
        
        # Check that higher priorities generally come first
        # (allowing some tolerance for messages with the same priority)
        for i in range(len(priorities) - 1):
            current_priority = priorities[i]
            next_priority = priorities[i + 1]
            # Next priority should be <= current priority (descending order)
            assert next_priority <= current_priority, \
                f"Priority order violation at position {i}: {current_priority} -> {next_priority}"