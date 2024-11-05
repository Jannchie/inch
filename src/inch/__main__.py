

from inch import Inch, InchPoolExecutor

import random
from time import sleep

if __name__ == "__main__":


    class TestTask(Inch):
        def __call__(self):
            while self.completed < self.total:
                self.completed += random.randint(1, 200)
                sleep(0.1)

    class TestTaskNoProgress(Inch):

        def __call__(self):
            completed = 0
            while completed < 1200:
                completed += random.randint(1, 200)
                sleep(0.1)

    with InchPoolExecutor() as executor:
        for i in range(20):
            if i % 5 == 0:
                executor.start_inch(TestTaskNoProgress(name=f"Task {i+1}"))
            else:
                executor.start_inch(TestTask(name=f"Task {i+1}", total=1000))
