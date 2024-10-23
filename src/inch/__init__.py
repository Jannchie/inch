from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
from abc import ABC, abstractmethod
from time import sleep
from rich.progress import (
    Progress,
    TaskID,
    BarColumn,
    TimeRemainingColumn,
    TextColumn,
    SpinnerColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TaskProgressColumn,
)

from rich import get_console

console = get_console()


class BaseTask(ABC):

    def __init__(self, name: str, total: int | None = None, completed: int = 0):
        self.name = name
        self.total = total
        self.completed = completed

    @abstractmethod
    def run(self):
        pass


# Inch class handling task execution and progress
class Inch:
    def __init__(self, name: str = "Inch", max_workers: int = 8):
        self.__progress: Progress = Progress(
            SpinnerColumn(style="yellow"),
            TextColumn(" {task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TaskProgressColumn(),
        )
        self.__running_tasks: dict[TaskID, BaseTask] = {}
        self.__tasks: list[BaseTask] = []
        self.__total_task_count = 0
        self.__completed_task_count = 0
        self.__overall_task_id = None
        self.__finish_event = threading.Event()
        self.__lock = threading.Lock()
        self.max_workers = max_workers
        self.name = name

    def add_task(self, task: BaseTask):
        self.__tasks.append(task)

    def __initialize_overall_progress(self):
        self.__total_task_count = len(self.__tasks)
        # Adding the overall progress bar
        self.__overall_task_id = self.__progress.add_task(
            self.name, total=self.__total_task_count
        )

    def run(self):
        started_at = datetime.now()
        console.print(f":rocket: Start running [bold]{self.name}[/bold]...")
        self.__initialize_overall_progress()
        progress_thread = threading.Thread(
            target=self.__update_task_progress, daemon=True
        )
        progress_thread.start()
        self.__run_tasks()
        self.__finish_event.set()
        progress_thread.join()
        finished_at = datetime.now()
        console.print(f":white_check_mark: Finished in {finished_at - started_at}")

    def __update_task_progress(self):
        self.__progress.start()
        while not self.__finish_event.is_set():
            # 加锁，避免多线程操作 running_tasks 时出现问题
            with self.__lock:
                for task_id, task in self.__running_tasks.items():
                    self.__progress.update(task_id, completed=task.completed)
            sleep(0.05)
        self.__progress.stop()

    def __run_tasks(self):

        def run_task(task: BaseTask):
            task_id = self.__progress.add_task(task.name, total=task.total)
            with self.__lock:
                self.__running_tasks[task_id] = task
            try:
                task.run()
            except Exception as e:
                console.print(f"Task {task.name} failed: {e}")
            self.__progress.update(task_id, completed=task.total)

            # Update the global progress bar when a task is completed
            with self.__lock:
                self.__completed_task_count += 1
                self.__progress.update(
                    self.__overall_task_id, completed=self.__completed_task_count
                )

            if task_id in self.__running_tasks:
                del self.__running_tasks[task_id]
            self.__progress.remove_task(task_id)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for task in self.__tasks:
                pool.submit(run_task, task)


if __name__ == "__main__":

    import random

    class TestTask(BaseTask):
        def run(self):
            while self.completed < self.total:
                self.completed += random.randint(1, 200)
                sleep(0.1)

    class TestTaskNoProgress(BaseTask):

        def run(self):
            completed = 0
            while completed < 5000:
                completed += random.randint(1, 200)
                sleep(0.1)

    inch = Inch()
    for i in range(20):
        if i % 5 == 0:
            inch.add_task(TestTaskNoProgress(name=f"Task {i+1}"))
        else:
            inch.add_task(TestTask(name=f"Task {i+1}", total=2500))

    inch.run()
