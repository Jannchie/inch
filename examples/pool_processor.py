import time

from inch import SyncInchPoolProcessor


def process(data: dict) -> None:
    time.sleep(0.1)
    print(f"Processing: {data}")


def data_generator():
    for i in range(100):
        yield {"id": i, "value": f"item_{i}"}


def main():
    print("Running with queue capacity limit of 5...")
    with SyncInchPoolProcessor(
        process,
        worker=3,
        max_queue_size=5,
    ) as p:
        for data in data_generator():
            p.submit(data)
            print(f"Submitted: {data}")


def main_unlimited():
    print("Running with unlimited queue capacity...")
    with SyncInchPoolProcessor(process, worker=3) as p:
        for data in data_generator():
            p.submit(data)


if __name__ == "__main__":
    main()
