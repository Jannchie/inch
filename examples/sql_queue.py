#!/usr/bin/env python3

import asyncio
import tempfile
from pathlib import Path

from inch.aio.queue import AsyncSQLQueue
from inch.queue import SyncSQLQueue


def sync_example():
    print("=== Sync SQLQueue Example ===")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "queue.db"
        connection_string = f"sqlite:///{db_path}"

        # Create queue
        queue = SyncSQLQueue(connection_string, "example_queue", max_retries=3)

        print("1. Adding messages to queue...")
        queue.enqueue("High priority task", priority=10, key="urgent")
        queue.enqueue("Medium priority task", priority=5, key="normal")
        queue.enqueue("Low priority task", priority=1, key="batch")

        # Batch enqueue
        items = [
            ("Batch task 1", 3),
            ("Batch task 2", 7),
            ("Batch task 3", 2),
        ]
        queue.enqueue_batch_with_keys([
            (item[0], item[1], "batch") for item in items
        ])

        print("2. Queue status:")
        status = queue.get_status()
        print(f"   Pending: {status.pending_count}")
        print(f"   Processing: {status.processing_count}")

        print("3. Processing messages by priority...")
        processed = 0
        while processed < 3:
            message = queue.dequeue(visibility_timeout=30)
            if message:
                print(f"   Processing: {message.data} (priority={message.priority}, key={message.key})")
                # Simulate work
                queue.ack(message.message_id)
                processed += 1
            else:
                break

        print("4. Processing batch messages with key prefix...")
        batch_messages = queue.dequeue_batch(limit=5, key_prefix="batch")
        print(f"   Found {len(batch_messages)} batch messages")

        for message in batch_messages:
            print(f"   Batch processing: {message.data} (priority={message.priority})")

        # Acknowledge all batch messages
        queue.ack_batch([msg.message_id for msg in batch_messages])

        print("5. Final queue status:")
        status = queue.get_status()
        print(f"   Pending: {status.pending_count}")
        print(f"   Processing: {status.processing_count}")
        print(f"   Success: {status.success_count}")
        print(f"   Dead letter: {status.dead_letter_count}")

        # Clean up
        queue.clear()


async def async_example():
    print("\n=== Async SQLQueue Example ===")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "async_queue.db"
        connection_string = f"sqlite+aiosqlite:///{db_path}"

        # Create async queue
        queue = AsyncSQLQueue(connection_string, "async_example_queue", max_retries=3)

        print("1. Adding messages to async queue...")
        await queue.enqueue("Async high priority task", priority=10, key="urgent")
        await queue.enqueue("Async medium priority task", priority=5, key="normal")
        await queue.enqueue("Async low priority task", priority=1, key="batch")

        # Batch enqueue
        items = [
            ("Async batch task 1", 3, "batch"),
            ("Async batch task 2", 7, "batch"),
            ("Async batch task 3", 2, "batch"),
        ]
        await queue.enqueue_batch_with_keys(items)

        print("2. Async queue status:")
        status = await queue.get_status()
        print(f"   Pending: {status.pending_count}")
        print(f"   Processing: {status.processing_count}")

        print("3. Processing messages concurrently...")

        async def process_message() -> bool:
            message = await queue.dequeue(visibility_timeout=30)
            if message:
                print(f"   Processing: {message.data} (priority={message.priority}, key={message.key})")
                # Simulate async work
                await asyncio.sleep(0.1)
                await queue.ack(message.message_id)
                return True
            return False

        # Process multiple messages concurrently
        tasks = [process_message() for _ in range(3)]
        results = await asyncio.gather(*tasks)
        processed = sum(results)
        print(f"   Processed {processed} messages")

        print("4. Processing remaining batch messages...")
        batch_messages = await queue.dequeue_batch(limit=5, key_prefix="batch")
        print(f"   Found {len(batch_messages)} batch messages")

        for message in batch_messages:
            print(f"   Batch processing: {message.data} (priority={message.priority})")

        # Acknowledge all batch messages
        await queue.ack_batch([msg.message_id for msg in batch_messages])

        print("5. Final async queue status:")
        status = await queue.get_status()
        print(f"   Pending: {status.pending_count}")
        print(f"   Processing: {status.processing_count}")
        print(f"   Success: {status.success_count}")
        print(f"   Dead letter: {status.dead_letter_count}")

        # Clean up
        await queue.clear()


def error_handling_example():
    print("\n=== Error Handling Example ===")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "error_queue.db"
        connection_string = f"sqlite:///{db_path}"

        # Create queue with low max_retries for demonstration
        queue = SyncSQLQueue(connection_string, "error_queue", max_retries=2)

        print("1. Adding a message that will fail...")
        queue.enqueue("Task that will fail", priority=1)

        for attempt in range(3):
            message = queue.dequeue()
            if message:
                print(f"   Attempt {attempt + 1}: Processing {message.data}")
                print(f"   Retry count: {message.retry_count}")

                # Simulate failure
                error_msg = f"Simulated error on attempt {attempt + 1}"
                queue.nack(message.message_id, error_msg)
                print(f"   Failed with: {error_msg}")

        print("2. Checking dead letter messages...")
        dead_messages = queue.get_dead_letter_messages()
        if dead_messages:
            for msg in dead_messages:
                print(f"   Dead letter: {msg.data}")
                print(f"   Final retry count: {msg.retry_count}")
                print(f"   Final error: {msg.error_message}")

        status = queue.get_status()
        print(f"3. Final status - Dead letter count: {status.dead_letter_count}")

        queue.clear()


if __name__ == "__main__":
    print("SQLQueue Examples")
    print("================")

    sync_example()
    asyncio.run(async_example())
    error_handling_example()

    print("\nAll examples completed!")
