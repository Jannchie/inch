# inch

Inch is a Python library specifically designed to manage and execute long-running tasks in batches using multithreading. It enhances the user experience by providing a visual progress display through the rich library. The name "Inch" reflects its core functionality, enabling tasks to "move along slowly and carefully," thus ensuring a systematic and controlled execution process.

## Features

- Define concurrent tasks using an abstract base class.
- Execute multiple tasks concurrently with adjustable worker threads.
- Track and display task progress using the rich library.

## Installation

Inch can be installed using pip:

```bash
pip install inch
```

## Usage

Here's an example of how to use Inch to execute a simple task:

```python
from inch import Inch, InchPoolExecutor
import random
from time import sleep

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

```
