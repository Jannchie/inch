import uuid
from dataclasses import dataclass

from rich import print

from inch.aio.queue.memory_queue import AsyncMemoryQueue


@dataclass
class Data:
    id: uuid.UUID
    content: str


queue = AsyncMemoryQueue[Data]()


async def main():
    # Demonstrate basic key functionality
    print("=== Key-based Queue Demo ===")

    # Add tasks with different keys
    await queue.enqueue(Data(id=uuid.uuid4(), content="User task 1"), priority=1, key="user:123")
    await queue.enqueue(Data(id=uuid.uuid4(), content="Admin task 1"), priority=2, key="admin:456")
    await queue.enqueue(Data(id=uuid.uuid4(), content="User task 2"), priority=1, key="user:789")
    await queue.enqueue(Data(id=uuid.uuid4(), content="General task"), priority=3)

    # Add batches with different keys and priorities
    await queue.enqueue_batch([Data(id=uuid.uuid4(), content="Batch user task 1")], priority=1, key="user:batch")
    await queue.enqueue_batch([Data(id=uuid.uuid4(), content="Batch admin task 1")], priority=2, key="admin:batch")
    await queue.enqueue_batch([Data(id=uuid.uuid4(), content="Batch general task")], priority=3, key=None)

    print("Queue status after adding tasks:")
    print(await queue.get_status())

    # Get tasks by specific key
    print("\n=== Getting tasks by specific key ===")
    user_task = await queue.dequeue(key="user:123")
    if user_task:
        print(f"Got user task: {user_task.data.content}")
        await queue.ack(user_task)

    # Get tasks by key prefix
    print("\n=== Getting tasks by key prefix ===")
    admin_tasks = await queue.dequeue_batch(limit=2, key_prefix="admin:")
    if admin_tasks:
        print(f"Got {len(admin_tasks)} admin tasks:")
        for task in admin_tasks:
            print(f"  - {task.data.content} (key: {task.key})")
        await queue.ack_batch(admin_tasks)

    # Get general tasks (no key specified)
    print("\n=== Getting general tasks ===")
    general_tasks = await queue.dequeue_batch(limit=2)
    if general_tasks:
        print(f"Got {len(general_tasks)} general tasks:")
        for task in general_tasks:
            print(f"  - {task.data.content} (key: {task.key})")
        await queue.ack_batch(general_tasks)

    print("\n=== Queue status by prefix ===")
    user_status = await queue.get_status(key_prefix="user:")
    print(f"User tasks status: {user_status}")

    all_status = await queue.get_status()
    print(f"All tasks status: {all_status}")

    # Process remaining tasks
    print("\n=== Processing remaining tasks ===")
    while remaining_tasks := await queue.dequeue_batch(5):
        print(f"Processing {len(remaining_tasks)} remaining tasks:")
        for task in remaining_tasks:
            print(f"  - {task.data.content} (key: {task.key})")
        await queue.ack_batch(remaining_tasks)

    print("\nFinal queue status:")
    print(await queue.get_status())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
