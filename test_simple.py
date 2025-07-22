#!/usr/bin/env python3

import tempfile
from pathlib import Path

from inch.queue.sql_queue import SyncSQLQueue


def simple_test():
    print("Testing SQLQueue...")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        connection_string = f"sqlite:///{db_path}"

        try:
            queue = SyncSQLQueue(connection_string, "test_queue")
            print("Queue created successfully")

            # Test basic enqueue
            queue.enqueue("test message")
            print("Message enqueued")

            # Test status
            status = queue.get_status()
            print(f"Status: pending={status.pending_count}")

            # Test dequeue
            message = queue.dequeue()
            if message:
                print(f"Dequeued: {message.data}")
                queue.ack(message.message_id)
                print("Message acknowledged")

            final_status = queue.get_status()
            print(f"Final status: success={final_status.success_count}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    simple_test()
