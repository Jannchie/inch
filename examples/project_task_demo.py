#!/usr/bin/env python3
"""
Project Task Queue Demo

This demo shows how to use the improved queue system for project-based
task management with priorities, statistics, and concurrent processing.
"""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

from task_manager import TaskData, TaskManager


@dataclass
class DemoConfig:
    """Configuration for the demo"""
    num_projects: int = 3
    task_types_per_project: int = 3
    tasks_per_type: int = 10
    concurrent_workers: int = 4
    processing_delay_range: tuple[float, float] = (0.1, 0.5)
    failure_rate: float = 0.1


async def create_demo_tasks(manager: TaskManager, config: DemoConfig) -> None:
    """
    Create demo tasks for multiple projects with different priorities

    Args:
        manager: TaskManager instance
        config: Demo configuration
    """
    print("📦 Creating demo tasks...")

    task_types = ["data_processing", "model_training", "report_generation"]
    priorities = {"data_processing": 10, "model_training": 5, "report_generation": 1}

    total_tasks = 0

    for project_id in range(1, config.num_projects + 1):
        project_name = f"project_{project_id}"

        for task_type in task_types[:config.task_types_per_project]:
            priority = priorities.get(task_type, 0)

            for task_num in range(config.tasks_per_type):
                task_data = TaskData(
                    task_id=f"{project_name}_{task_type}_{task_num}",
                    project_id=project_name,
                    task_type=task_type,
                    data={
                        "input_file": f"/data/{project_name}/input_{task_num}.json",
                        "output_file": f"/data/{project_name}/output_{task_num}.json",
                        "parameters": {"batch_size": 32, "epochs": 10},
                    },
                    created_at=time.time(),
                    metadata={"created_by": "demo_producer", "version": "1.0"},
                )

                await manager.enqueue_task(task_data, priority=priority)
                total_tasks += 1

    print(f"✅ Created {total_tasks} tasks across {config.num_projects} projects")


async def worker(
    worker_id: int,
    manager: TaskManager,
    config: DemoConfig,
    stats: dict[str, Any],
) -> None:
    """
    Worker coroutine that processes tasks

    Args:
        worker_id: Unique worker identifier
        manager: TaskManager instance
        config: Demo configuration
        stats: Shared statistics dictionary
    """
    print(f"🔧 Worker {worker_id} started")
    processed = 0

    while True:
        # Dequeue a task (returns Message object)
        message = await manager.queue.dequeue(visibility_timeout=1)

        if message is None:
            # No more tasks available
            break

        task_data = message.data
        message_id = message.message_id

        print(f"⚙️  Worker {worker_id} processing {task_data.task_id} "
              f"(priority: {message.priority}, project: {task_data.project_id})")

        # Simulate processing time
        delay = random.uniform(*config.processing_delay_range)
        await asyncio.sleep(delay)

        # Simulate occasional failures
        success = random.random() > config.failure_rate

        if success:
            print(f"✅ Worker {worker_id} completed {task_data.task_id}")
            await manager.complete_task(message_id, success=True)
            stats["successful"] += 1
        else:
            print(f"❌ Worker {worker_id} failed {task_data.task_id}")
            await manager.complete_task(message_id, success=False)
            stats["failed"] += 1

        processed += 1

    print(f"🏁 Worker {worker_id} finished, processed {processed} tasks")


async def monitor_stats(manager: TaskManager, config: DemoConfig) -> None:
    """
    Monitor and display real-time statistics

    Args:
        manager: TaskManager instance
        config: Demo configuration
    """
    print("\n📊 Starting statistics monitor...")

    while True:
        # Get overall stats
        overall_stats = await manager.get_all_stats()

        if overall_stats.total_count == 0:
            await asyncio.sleep(0.5)
            continue

        print("\n📈 Overall Statistics:")
        print(f"   Total: {overall_stats.total_count}")
        print(f"   Pending: {overall_stats.pending_count}")
        print(f"   Processing: {overall_stats.processing_count}")
        print(f"   Completed: {overall_stats.success_count}")
        print(f"   Failed: {overall_stats.failed_count}")

        # Show per-project stats
        for project_id in range(1, config.num_projects + 1):
            project_name = f"project_{project_id}"
            project_stats = await manager.get_project_stats(project_name)

            if project_stats.total_count > 0:
                print(f"   📁 {project_name}: "
                      f"P:{project_stats.pending_count} "
                      f"R:{project_stats.processing_count} "
                      f"C:{project_stats.success_count} "
                      f"F:{project_stats.failed_count}")

        # Stop monitoring when all tasks are done
        if (overall_stats.pending_count == 0 and
            overall_stats.processing_count == 0):
            break

        await asyncio.sleep(1.0)


async def demonstrate_batch_processing(manager: TaskManager) -> None:
    """
    Demonstrate batch processing capabilities

    Args:
        manager: TaskManager instance
    """
    print("\n🔄 Demonstrating batch processing...")

    # Create some additional tasks for batch processing
    batch_tasks = []
    for i in range(5):
        task_data = TaskData(
            task_id=f"batch_task_{i}",
            project_id="batch_project",
            task_type="batch_processing",
            data={"batch_id": i, "items": list(range(i*10, (i+1)*10))},
            created_at=time.time(),
        )
        batch_tasks.append(task_data)

    # Enqueue batch tasks
    for task in batch_tasks:
        await manager.enqueue_task(task, priority=3)

    # Process in batches
    batch_size = 3
    messages = await manager.queue.dequeue_batch(
        limit=batch_size,
        key_prefix="project_batch_project:",
    )

    print(f"📦 Dequeued batch of {len(messages)} tasks")

    # Process batch
    message_ids = []
    for message in messages:
        task_data = message.data
        print(f"   🔧 Processing batch task {task_data.task_id}")
        # Simulate batch processing
        await asyncio.sleep(0.1)
        message_ids.append(message.message_id)

    # Batch acknowledge
    await manager.queue.ack_batch(message_ids)
    print(f"✅ Batch completed: acknowledged {len(message_ids)} tasks")


async def main() -> None:
    """Main demo function"""
    print("🚀 Project Task Queue Demo")
    print("=" * 50)

    config = DemoConfig()
    manager = TaskManager[TaskData]()

    # Shared statistics for workers
    worker_stats = {"successful": 0, "failed": 0}

    # Create demo tasks
    await create_demo_tasks(manager, config)

    # Start monitoring in background
    monitor_task = asyncio.create_task(monitor_stats(manager, config))

    # Start workers
    print(f"\n🔧 Starting {config.concurrent_workers} workers...")
    worker_tasks = [
        asyncio.create_task(worker(i, manager, config, worker_stats))
        for i in range(config.concurrent_workers)
    ]

    # Wait for all workers to complete
    await asyncio.gather(*worker_tasks)

    # Wait for monitoring to finish
    await monitor_task

    # Demonstrate batch processing
    await demonstrate_batch_processing(manager)

    # Final statistics
    print("\n🎯 Final Results:")
    print(f"   Successfully processed: {worker_stats['successful']} tasks")
    print(f"   Failed: {worker_stats['failed']} tasks")

    # Show final per-project statistics
    for project_id in range(1, config.num_projects + 1):
        project_name = f"project_{project_id}"
        stats = await manager.get_project_stats(project_name)
        print(f"   📁 {project_name}: {stats.success_count} completed, "
              f"{stats.failed_count} failed")

    print("\n" + "=" * 50)
    print("🎉 Demo completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
