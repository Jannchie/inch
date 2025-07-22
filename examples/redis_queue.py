"""
Redis Queue Example

This example demonstrates how to use the Redis-based queue implementation
for distributed task processing across multiple machines.
"""

import asyncio
import time

import redis
import redis.asyncio as aioredis

from inch.aio.queue.redis_queue import AsyncRedisQueue
from inch.queue.redis_queue import SyncRedisQueue


def sync_example():
    """Synchronous Redis queue example."""
    print("=== Synchronous Redis Queue Example ===")

    # Create Redis client
    redis_client = redis.Redis(host="192.168.31.59", port=6379, db=0, decode_responses=True)

    # Create queue
    queue = SyncRedisQueue(
        redis_client=redis_client,
        queue_name="example_queue",
        max_retries=3,
    )

    # Clear queue for demo
    queue.clear()

    # Enqueue some tasks with different priorities
    print("Enqueuing tasks...")
    queue.enqueue("Low priority task", priority=1)
    queue.enqueue("High priority task", priority=10)
    queue.enqueue("Medium priority task", priority=5)

    # Enqueue tasks with keys for partitioning
    queue.enqueue("User 1 task", key="user:1", priority=3)
    queue.enqueue("User 2 task", key="user:2", priority=7)

    # Show queue status
    status = queue.get_status()
    print(f"Queue status: {status.pending_count} pending, {status.processing_count} processing")

    # Process tasks in priority order
    print("\nProcessing tasks...")
    while True:
        message = queue.dequeue(visibility_timeout=30)
        if message is None:
            break

        print(f"Processing: {message.data} (priority: {message.priority}, key: {message.key})")

        # Simulate work
        time.sleep(0.1)

        # Acknowledge task completion
        queue.ack(message.message_id)

    # Show final status
    final_status = queue.get_status()
    print(f"Final status: {final_status.success_count} successful")

    redis_client.close()


async def async_example():
    """Asynchronous Redis queue example."""
    print("\n=== Asynchronous Redis Queue Example ===")

    # Create async Redis client
    redis_client = aioredis.Redis(host="192.168.31.59", port=6379, db=1, decode_responses=True)

    # Create async queue
    queue = AsyncRedisQueue(
        redis_client=redis_client,
        queue_name="async_example_queue",
        max_retries=3,
    )

    # Clear queue for demo
    await queue.clear()

    # Batch enqueue with keys
    print("Batch enqueuing tasks...")
    batch_items = [
        ("Async task 1", 5, "worker:1"),
        ("Async task 2", 8, "worker:2"),
        ("Async task 3", 3, "worker:1"),
        ("General async task", 6, None),
    ]
    await queue.enqueue_batch_with_keys(batch_items)

    # Show queue status
    status = await queue.get_status()
    print(f"Queue status: {status.pending_count} pending")

    # Process tasks from specific worker partition
    print("\nProcessing worker:1 tasks...")
    worker1_messages = await queue.dequeue_batch(limit=5, key_prefix="worker:1")
    for message in worker1_messages:
        print(f"Worker 1 processing: {message.data} (priority: {message.priority})")

    # Batch acknowledge
    if worker1_messages:
        await queue.ack_batch([msg.message_id for msg in worker1_messages])

    # Process remaining tasks
    print("\nProcessing remaining tasks...")
    remaining_messages = await queue.dequeue_batch(limit=10)
    for message in remaining_messages:
        print(f"Processing: {message.data} (key: {message.key})")
        await queue.ack(message.message_id)

    # Show final status
    final_status = await queue.get_status()
    print(f"Final status: {final_status.success_count} successful")

    await redis_client.aclose()


async def distributed_example():
    """Example showing how Redis queues work across distributed systems."""
    print("\n=== Distributed Processing Example ===")

    # Simulate multiple workers sharing the same Redis queue
    redis_client1 = aioredis.Redis(host="192.168.31.59", port=6379, db=2, decode_responses=True)
    redis_client2 = aioredis.Redis(host="192.168.31.59", port=6379, db=2, decode_responses=True)

    # Create queues for different "workers"
    producer_queue = AsyncRedisQueue(redis_client1, "distributed_queue")
    consumer_queue = AsyncRedisQueue(redis_client2, "distributed_queue")

    await producer_queue.clear()

    # Producer: enqueue many tasks
    print("Producer: Adding tasks to distributed queue...")
    tasks = [(f"Task {i}", i % 10) for i in range(20)]
    await producer_queue.enqueue_batch(tasks)

    # Consumer: process tasks (simulating a different machine/process)
    print("Consumer: Processing tasks from distributed queue...")
    processed = 0
    while processed < 10:  # Process half the tasks
        messages = await consumer_queue.dequeue_batch(limit=3)
        if not messages:
            break

        for message in messages:
            print(f"Consumer processed: {message.data}")
            await consumer_queue.ack(message.message_id)
            processed += 1

    # Show remaining tasks (would be processed by other workers)
    status = await producer_queue.get_status()
    print(f"Remaining tasks for other workers: {status.pending_count}")

    await redis_client1.aclose()
    await redis_client2.aclose()


def error_handling_example():
    """Example showing error handling and retry mechanisms."""
    print("\n=== Error Handling and Retry Example ===")

    redis_client = redis.Redis(host="192.168.31.59", port=6379, db=3, decode_responses=True)
    queue = SyncRedisQueue(redis_client, "error_queue", max_retries=2)

    queue.clear()

    # Enqueue a task that will "fail"
    queue.enqueue("Task that will fail")

    # Simulate processing with failures
    for attempt in range(3):
        message = queue.dequeue()
        if message:
            print(f"Attempt {attempt + 1}: Processing {message.data}")

            if attempt < 2:  # Simulate failure for first 2 attempts
                print(f"  Failed! (retry count: {message.retry_count})")
                queue.nack(message.message_id, error=f"Simulated error on attempt {attempt + 1}")
            else:
                print("  Success!")
                queue.ack(message.message_id)
        else:
            print("No more messages to process")
            break

    # Check dead letter queue
    dead_letters = queue.get_dead_letter_messages()
    if dead_letters:
        print(f"Tasks moved to dead letter queue: {len(dead_letters)}")
        for dl_msg in dead_letters:
            print(f"  Dead letter: {dl_msg.data} (error: {dl_msg.error_message})")

    redis_client.close()


def main():
    """Run all examples."""
    try:
        # Test connection first
        test_client = redis.Redis(host="192.168.31.59", port=6379, decode_responses=True)
        test_client.ping()
        print("Connected to Redis successfully!")
        test_client.close()

        # Run examples
        sync_example()
        asyncio.run(async_example())
        asyncio.run(distributed_example())
        error_handling_example()

    except redis.ConnectionError:
        print("Could not connect to Redis at 192.168.31.59:6379")
        print("Please ensure Redis is running and accessible.")
    except Exception as e:
        print(f"Error running examples: {e}")


if __name__ == "__main__":
    main()
