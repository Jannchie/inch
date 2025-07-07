import asyncio
import time
from dataclasses import dataclass

from rich import print

from inch.executor import AsyncInchPoolExecutor


@dataclass
class WorkItem:
    task_id: int
    duration: float


def process_task(item: WorkItem) -> str:
    time.sleep(item.duration)
    return f"Task {item.task_id} completed after {item.duration}s"


async def async_process_task(item: WorkItem) -> str:
    await asyncio.sleep(item.duration)
    return f"Async task {item.task_id} completed after {item.duration}s"


async def test_individual_submit(executor: AsyncInchPoolExecutor[WorkItem, str]) -> None:
    """Test individual async submit for fine-grained control"""
    print("\nTesting individual async submit...")
    individual_start = time.time()

    # Submit tasks individually and await them as they complete
    task1 = executor.submit(async_process_task, WorkItem(task_id=14, duration=0.5))
    task2 = executor.submit(process_task, WorkItem(task_id=15, duration=0.3))
    task3 = executor.submit(async_process_task, WorkItem(task_id=16, duration=0.2))

    # Wait for all tasks to complete
    result1 = await task1
    result2 = await task2
    result3 = await task3

    individual_end = time.time()

    print("Individual results:")
    print(f"  {result1}")
    print(f"  {result2}")
    print(f"  {result3}")
    print(f"Individual tasks completed in {individual_end - individual_start:.2f} seconds")


async def main() -> None:
    # Create the executor
    async with AsyncInchPoolExecutor(max_workers=2) as executor:
        # Submit some tasks
        work_items = [
            WorkItem(task_id=1, duration=1.0),
            WorkItem(task_id=2, duration=0.5),
            WorkItem(task_id=3, duration=1.5),
            WorkItem(task_id=4, duration=0.8),
            WorkItem(task_id=5, duration=0.3),
        ]

        print("Submitting tasks...")
        start_time = time.time()

        # Submit tasks concurrently using map for better performance
        print("\nSubmitting tasks concurrently...")
        results = await executor.map(process_task, work_items)
        for result in results:
            print(f"Result: {result}")

        end_time = time.time()
        print(f"\nAll tasks completed in {end_time - start_time:.2f} seconds")

        # Test map method
        print("\nTesting map method...")
        map_items = [
            WorkItem(task_id=6, duration=0.3),
            WorkItem(task_id=7, duration=0.2),
            WorkItem(task_id=8, duration=0.2),
        ]

        map_start = time.time()
        results = await executor.map(process_task, map_items)
        map_end = time.time()

        print("Map results:")
        for result in results:
            print(f"  {result}")
        print(f"Map completed in {map_end - map_start:.2f} seconds")

        # Test async functions
        print("\nTesting async functions...")
        async_items = [
            WorkItem(task_id=9, duration=0.3),
            WorkItem(task_id=10, duration=0.2),
            WorkItem(task_id=11, duration=0.2),
        ]

        async_start = time.time()
        async_results = await executor.map(async_process_task, async_items)
        async_end = time.time()

        print("Async results:")
        for result in async_results:
            print(f"  {result}")
        print(f"Async tasks completed in {async_end - async_start:.2f} seconds")

        # Test mixed sync and async with map
        print("\nTesting mixed sync and async functions...")
        mixed_items = [
            WorkItem(task_id=12, duration=0.15),
            WorkItem(task_id=13, duration=0.25),
        ]

        mixed_start = time.time()
        sync_results = await executor.map(process_task, mixed_items)
        async_results = await executor.map(async_process_task, mixed_items)
        mixed_end = time.time()

        print("Mixed results:")
        print("  Sync results:")
        for result in sync_results:
            print(f"    {result}")
        print("  Async results:")
        for result in async_results:
            print(f"    {result}")
        print(f"Mixed tasks completed in {mixed_end - mixed_start:.2f} seconds")

        # Test individual async submit for fine-grained control
        await test_individual_submit(executor)


if __name__ == "__main__":
    asyncio.run(main())
