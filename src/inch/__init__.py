import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from queue import Queue

from rich import get_console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

console = get_console()


class Inch(ABC):
    def __init__(self, name: str, total: int | None = None, completed: int = 0) -> None:
        self.name = name
        self.total = total
        self.completed = completed

    @abstractmethod
    def __call__(self) -> None:
        pass


# Inch class handling task execution and progress
class InchPoolExecutor:
    def __init__(self, name: str = "Inch", max_workers: int = 8) -> None:
        self.__progress: Progress = Progress(
            SpinnerColumn(style="yellow"),
            TextColumn(" {task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TaskProgressColumn(),
        )
        self.__running_tasks: dict[TaskID, Inch] = {}
        self.__pending_tasks: Queue[Inch] = Queue()
        self.__total_task_count = 0
        self.__completed_task_count = 0
        self.__overall_task_id = None
        self.__finish_event = threading.Event()
        self.__lock = threading.Lock()
        self.max_workers = max_workers
        self.name = name

    def __enter__(self) -> "InchPoolExecutor":
        # Initialize the progress bar
        self.started_at = datetime.now(UTC)
        console.print(f":rocket: Start running [bold]{self.name}[/bold]...")
        self.__initialize_overall_progress()
        self.progress_thread = threading.Thread(target=self.__update_task_progress, daemon=True)
        self.__main_thread = threading.Thread(target=self.__run_tasks, daemon=True)
        self.__main_thread.start()
        self.progress_thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.__finish_event.wait()
        self.__main_thread.join()
        self.progress_thread.join()
        self.finished_at = datetime.now(UTC)
        console.print(f":white_check_mark: Finished in {self.finished_at - self.started_at}")

    def start_inch(self, task: Inch) -> None:
        self.__total_task_count += 1
        if overall_task := self.__progress.tasks[self.__overall_task_id]:
            overall_task.total = self.__total_task_count
        self.__pending_tasks.put(task)

    def __initialize_overall_progress(self) -> None:
        # Adding the overall progress bar
        self.__overall_task_id = self.__progress.add_task(self.name, total=self.__total_task_count)

    def run(self):
        self.__enter__()
        self.__exit__(None, None, None)

    def __update_task_progress(self) -> None:
        self.__progress.start()
        while not self.__finish_event.wait(timeout=0.05):
            # 加锁，避免多线程操作 running_tasks 时出现问题
            with self.__lock:
                for task_id, task in self.__running_tasks.items():
                    self.__progress.update(task_id, completed=task.completed)
        self.__progress.stop()

    def __run_tasks(self) -> None:
        def run_task(task: Inch) -> None:
            task_id = self.__progress.add_task(task.name, total=task.total)
            with self.__lock:
                self.__running_tasks[task_id] = task
            try:
                task()
            except Exception as e:
                console.print(f"Task {task.name} failed: {e}")
            self.__progress.update(task_id, completed=task.total)

            # Update the global progress bar when a task is completed
            with self.__lock:
                self.__completed_task_count += 1
                self.__progress.update(self.__overall_task_id, completed=self.__completed_task_count)

            if task_id in self.__running_tasks:
                del self.__running_tasks[task_id]
            self.__progress.remove_task(task_id)
            self.__running_tasks = {k: v for k, v in self.__running_tasks.items() if v.completed != v.total}
            if not self.__running_tasks and self.__pending_tasks.empty():
                self.__finish_event.set()
                # Add a None to the queue to stop the worker threads
                self.__pending_tasks.put(None)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self.__finish_event.is_set():
                task = self.__pending_tasks.get()
                if not task:
                    break
                pool.submit(run_task, task)
