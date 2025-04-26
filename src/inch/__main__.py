import random
from time import sleep

from inch import Inch, InchPoolExecutor

if __name__ == "__main__":

    class TestTask(Inch):
        def __call__(self) -> None:
            while self.completed < self.total:
                self.completed += random.randint(1, 200)
                sleep(0.1)

    def func_task() -> None:
        completed = 0
        while completed < 1200:
            completed += random.randint(1, 200)
            sleep(0.1)

    with InchPoolExecutor() as executor:
        for i in range(20):
            if i % 5 == 0:
                executor.submit(func_task)
            else:
                executor.submit(TestTask(name=f"Task {i + 1}", total=1000))
