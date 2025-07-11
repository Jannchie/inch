#!/usr/bin/env python3
"""
Example usage of AsyncInchPoolProcessor

This example demonstrates how to use AsyncInchPoolProcessor for batch processing
of items using both synchronous and asynchronous processing functions.
"""

import asyncio
import time
from dataclasses import dataclass


@dataclass
class WorkItem:
    id: int
    data: str
    processed_at: float | None = None
    processor_name: str | None = None


async def main():
    """Main function demonstrating AsyncInchPoolProcessor usage"""
    from inch.aio.processor import AsyncInchPoolProcessor

    print("🚀 AsyncInchPoolProcessor Example")
    print("=" * 50)

    # Create some work items
    work_items = [WorkItem(i, f"Task {i}") for i in range(20)]
    print(f"📦 Created {len(work_items)} work items")

    # Example 1: Using synchronous processing function
    print("\n📝 Example 1: Synchronous Processing Function")
    print("-" * 40)

    def sync_process_task(item: WorkItem) -> None:
        """Synchronous processing function"""
        # Simulate some work
        time.sleep(0.1)
        item.processed_at = time.time()
        item.processor_name = "sync_processor"
        print(f"  ✅ Processed item {item.id} (sync)")

    start_time = time.time()
    async with AsyncInchPoolProcessor(
        process_func=sync_process_task,
        worker=4,
        show_progress=True,
    ) as processor:
        # Submit all items for processing
        for item in work_items:
            await processor.submit(item)

    sync_duration = time.time() - start_time
    print(f"⏱️  Synchronous processing completed in {sync_duration:.2f}s")

    # Reset items for next example
    for item in work_items:
        item.processed_at = None
        item.processor_name = None

    # Example 2: Using asynchronous processing function
    print("\n🔄 Example 2: Asynchronous Processing Function")
    print("-" * 40)

    async def async_process_task(item: WorkItem) -> None:
        """Asynchronous processing function"""
        # Simulate some async work
        await asyncio.sleep(0.1)
        item.processed_at = time.time()
        item.processor_name = "async_processor"
        print(f"  ✅ Processed item {item.id} (async)")

    start_time = time.time()
    async with AsyncInchPoolProcessor(
        process_func=async_process_task,
        worker=4,
        show_progress=True,
    ) as processor:
        # Submit all items for processing
        for item in work_items:
            await processor.submit(item)

    async_duration = time.time() - start_time
    print(f"⏱️  Asynchronous processing completed in {async_duration:.2f}s")

    # Example 3: Error handling
    print("\n❌ Example 3: Error Handling")
    print("-" * 40)

    error_items = [WorkItem(i, f"Error Task {i}") for i in range(5)]

    def failing_process_task(item: WorkItem) -> None:
        """Processing function that fails for certain items"""
        if item.id % 2 == 0:
            msg = f"Simulated failure for item {item.id}"
            raise ValueError(msg)

        item.processed_at = time.time()
        item.processor_name = "error_processor"
        print(f"  ✅ Successfully processed item {item.id}")

    async with AsyncInchPoolProcessor(
        process_func=failing_process_task,
        worker=2,
        show_progress=True,
    ) as processor:
        # Submit all items for processing
        for item in error_items:
            await processor.submit(item)

    successful_items = [item for item in error_items if item.processed_at is not None]
    failed_items = [item for item in error_items if item.processed_at is None]

    print(f"✅ Successfully processed: {len(successful_items)} items")
    print(f"❌ Failed to process: {len(failed_items)} items")

    # Example 4: Custom queue with size limit
    print("\n📦 Example 4: Custom Queue with Size Limit")
    print("-" * 40)

    from inch.aio.queue.memory_queue import AsyncMemoryQueue

    # Create a custom queue with a size limit
    custom_queue = AsyncMemoryQueue[WorkItem](max_size=5)
    limited_items = [WorkItem(i, f"Limited Task {i}") for i in range(10)]

    async def slow_process_task(item: WorkItem) -> None:
        """Slow processing function to demonstrate queue limiting"""
        await asyncio.sleep(0.2)
        item.processed_at = time.time()
        item.processor_name = "slow_processor"
        print(f"  ✅ Slowly processed item {item.id}")

    async with AsyncInchPoolProcessor(
        process_func=slow_process_task,
        worker=2,
        queue=custom_queue,
        show_progress=True,
    ) as processor:
        # Submit items concurrently to demonstrate queue blocking
        submission_tasks = [processor.submit(item) for item in limited_items]
        await asyncio.gather(*submission_tasks)

    print(f"✅ All {len(limited_items)} items processed through limited queue")

    # Example 5: Batch processing with concurrent submission
    print("\n⚡ Example 5: Concurrent Batch Processing")
    print("-" * 40)

    batch_items = [WorkItem(i, f"Batch Task {i}") for i in range(50)]

    async def fast_process_task(item: WorkItem) -> None:
        """Fast processing function"""
        await asyncio.sleep(0.05)
        item.processed_at = time.time()
        item.processor_name = "fast_processor"

    start_time = time.time()
    async with AsyncInchPoolProcessor(
        process_func=fast_process_task,
        worker=8,
        show_progress=True,
    ) as processor:
        # Submit all items concurrently
        submission_tasks = [processor.submit(item) for item in batch_items]
        await asyncio.gather(*submission_tasks)

    batch_duration = time.time() - start_time
    processed_count = len([item for item in batch_items if item.processed_at is not None])

    print(f"⚡ Processed {processed_count} items in {batch_duration:.2f}s")
    print(f"🚀 Processing rate: {processed_count / batch_duration:.1f} items/second")

    # Example 6: Progress tracking disabled
    print("\n🔇 Example 6: Processing without Progress Display")
    print("-" * 40)

    quiet_items = [WorkItem(i, f"Quiet Task {i}") for i in range(10)]

    async with AsyncInchPoolProcessor(
        process_func=async_process_task,
        worker=4,
        show_progress=False,  # Disable progress display
    ) as processor:
        for item in quiet_items:
            await processor.submit(item)

    print(f"🔇 Processed {len(quiet_items)} items quietly")

    print("\n" + "=" * 50)
    print("🎉 All examples completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
