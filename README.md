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
from inch import Inch, Task

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

inch = Inch(max_workers=8)
for i in range(20):
    inch.add_task(TestTask(name=f"Task {i+1}", total=1000))

inch.run()
```
