import uuid
from dataclasses import dataclass

from rich import print

from inch.queue.memory_queue import AsyncMemoryQueue


@dataclass
class Data:
    id: uuid.UUID


queue = AsyncMemoryQueue[Data]()


async def main():
    data = [Data(id=uuid.uuid4()) for _ in range(10)]
    for datum in data:
        await queue.enqueue(datum)
    while message := await queue.dequeue():
        print(message)
        await queue.ack(message)
    print(await queue.get_status())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
