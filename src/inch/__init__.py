from concurrent.futures import ThreadPoolExecutor
import random
import rich
import threading
from abc import ABC, abstractmethod
from time import sleep
from rich.progress import Progress, TaskID


class Task(ABC):

    def __init__(self, name: str, total: int = 100):
        self.name = name
        self.total = total

    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def get_progress(self) -> int:
        pass


class Inch:
    def __init__(self, max_workers: int = 8):
        self.__progress: Progress = Progress()
        self.__running_tasks: dict[TaskID, Task] = {}
        self.__tasks: list[Task] = []
        self.__finish_event = threading.Event()
        self.__lock = threading.Lock()
        self.max_workers = max_workers

    def add_task(self, task: Task):
        self.__tasks.append(task)

    def run(self):

        progress_thread = threading.Thread(target=self.__update_task_progress)
        progress_thread.start()
        self.__run_tasks()
        self.__finish_event.set()
        progress_thread.join()

    def __update_task_progress(self):
        self.__progress.start()
        while not self.__finish_event.is_set():
            # 加锁，避免多线程操作 running_tasks 时出现问题
            with self.__lock:
                for task_id, task in self.__running_tasks.items():
                    self.__progress.update(task_id, completed=task.get_progress())
            sleep(0.1)
        self.__progress.stop()

    def __run_tasks(self):

        def run_task(task: Task):
            rich.print(f"Running task: {task.name}")
            task_id = self.__progress.add_task(task.name, total=task.total)
            with self.__lock:
                self.__running_tasks[task_id] = task
            task.run()
            self.__progress.update(task_id, completed=task.total)
            if task_id in self.__running_tasks:
                del self.__running_tasks[task_id]
            self.__progress.remove_task(task_id)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for task in self.__tasks:
                pool.submit(run_task, task)


if __name__ == "__main__":

    class TestTask(Task):

        def __init__(self, name: str, total: int):
            super().__init__(name, total)
            self.progress = 0

        def run(self):
            while self.progress < self.total:
                self.progress += random.randint(1, 200)
                sleep(0.1)

        def get_progress(self) -> int:
            return self.progress

    inch = Inch()
    for i in range(20):
        inch.add_task(TestTask(name=f"Task {i+1}", total=1000))

    inch.run()
