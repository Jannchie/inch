#!/usr/bin/env python3

import tempfile
import traceback
from pathlib import Path

from inch.queue.sql_queue import SyncSQLQueue


def simple_test():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        try:
            queue = SyncSQLQueue(connection_string, "test_queue")

            # Test basic enqueue
            queue.enqueue("test message")

            # Test status
            queue.get_status()

            # Test dequeue
            message = queue.dequeue()
            if message:
                queue.ack(message.message_id)

            queue.get_status()

        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    simple_test()
