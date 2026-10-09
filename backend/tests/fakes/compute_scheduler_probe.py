"""Real socket/scheduler with injected execution, kept separate from GPU evidence."""

import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from printstash_core.inference import EmbeddingError

from app.runtime.compute import client
from app.runtime.compute.broker import serve
from app.runtime.compute.contracts import ComputeMode
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import InferenceRequest, Priority


def main():
    root = Path(sys.argv[1])
    client.bind(root)
    address = client.directory()
    stop = threading.Event()
    batches = []

    class Executor(Dispatcher):
        def __init__(self, root, **kwargs):
            super().__init__(
                root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
            )

        def pressure(self):
            return stop.is_set()

        def execute(self, request):
            data = json.loads(base64.b64decode(request.payload))
            items = data["inputs"]
            batches.append(len(items))
            if any(item["text"] == "bad" for item in items):
                raise EmbeddingError("embedding_input_invalid")
            return json.dumps(
                {
                    "config_hash": data["config_hash"],
                    "vectors": [[float(item["text"])] for item in items],
                    "truncated": [False] * len(items),
                }
            ).encode()

    server = threading.Thread(
        target=serve, args=(address,), kwargs={"dispatcher_factory": Executor}
    )
    server.start()
    until = time.monotonic() + 5
    while not (address / "broker.sock").exists():
        if time.monotonic() > until:
            raise RuntimeError("probe_startup")
        time.sleep(0.005)

    def request(value, barrier=None):
        if barrier is not None:
            barrier.wait(timeout=2)
        payload = json.dumps(
            {"config_hash": "a" * 64, "inputs": [{"modality": "text", "text": value}]}
        ).encode()
        message = InferenceRequest(
            deadline=time.monotonic() + 3,
            priority=Priority.BACKGROUND,
            request_id=value,
            directory="/models",
            model_key="contract",
            threads=1,
            payload=base64.b64encode(payload).decode(),
        )
        response = json.loads(client.exchange(message))
        return json.loads(base64.b64decode(response["result"]))

    try:
        barrier = threading.Barrier(4)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(
                workers.map(
                    lambda value: request(value, barrier), ["0", "1", "bad", "3"]
                )
            )
        started = time.monotonic()
        singleton = request("4")
        elapsed = time.monotonic() - started
        print(
            json.dumps(
                {
                    "batches": batches,
                    "results": results,
                    "singleton": singleton,
                    "elapsed": elapsed,
                }
            )
        )
    finally:
        stop.set()
        server.join(timeout=5)
        if server.is_alive():
            raise RuntimeError("probe_shutdown")


if __name__ == "__main__":
    main()
