"""
Example demonstrating the use of AsyncInchPoolExecutor.

This module shows how to use the asynchronous version of the InchPoolExecutor
with both class-based AsyncInch tasks and function-based async tasks.
"""

import asyncio
import random

from inch.processor import AsyncInch, AsyncInchPoolExecutor


class AsyncTestTask(AsyncInch):
    """
    An example AsyncInch task that simulates work with random progress increments.
    """

    async def __call__(self) -> None:
        """
        Execute the async task, incrementing progress randomly.
        """
        while self.completed < self.total:
            # Simulate work with random progress
            self.completed += random.randint(1, 200)
            # Cap at total
            self.completed = min(self.completed, self.total)
            # Simulate async IO operation
            await asyncio.sleep(0.1)


async def async_func_task(inch: AsyncInch) -> None:
    """
    An example async function task that simulates work.

    Args:
        inch: The AsyncInch instance for progress tracking
    """
    # Set the total work units
    inch.total = 1200

    while inch.completed < inch.total:
        # Simulate work with random progress
        inch.completed += random.randint(1, 200)
        # Cap at total
        inch.completed = min(inch.completed, inch.total)
        # Simulate async IO operation
        await asyncio.sleep(0.1)


async def slow_task(inch: AsyncInch) -> None:
    """
    A slower async task to demonstrate concurrent execution.

    Args:
        inch: The AsyncInch instance for progress tracking
    """
    inch.total = 800

    for _ in range(inch.total // 100):
        # Simulate larger chunks of work
        inch.completed += 100
        # Simulate longer IO operation
        await asyncio.sleep(0.3)


async def main() -> None:
    """
    Main async function to demonstrate the AsyncInchPoolExecutor.
    """
    # Create an executor with a custom name and concurrency level
    async with AsyncInchPoolExecutor(name="Async Tasks Demo", max_concurrency=10) as executor:
        # Submit a mix of class-based and function-based async tasks
        for i in range(20):
            if i % 5 == 0:
                # Submit function-based tasks
                executor.submit(async_func_task)
            elif i % 7 == 0:
                # Submit a slower task
                executor.submit(slow_task)
            else:
                # Submit class-based tasks
                executor.submit(AsyncTestTask(name=f"Async Task {i + 1}", total=1000))

        await executor.wait()


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
