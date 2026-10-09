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
from app.runtime.compute.protocol import InferenceRequest, Priority, StatusRequest


def main():
    root = Path(sys.argv[1])
    client.bind(root)
    address = client.directory()
    stop = threading.Event()
    batches = []
    gate_started = threading.Event()
    release_gate = threading.Event()

    class Executor(Dispatcher):
        def __init__(self, root, **kwargs):
            super().__init__(
                root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
            )

        def pressure(self):
            return stop.is_set()

        def execute(self, request):
            if request.request_id == "gate":
                gate_started.set()
                if not release_gate.wait(timeout=5):
                    raise RuntimeError("probe_gate_timeout")
                return json.dumps({"vectors": [[99.0]]}).encode()
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
        # Queue all four READY requests behind an executing item. This tests
        # coalescing deterministically, not OS thread arrival within 10 ms.
        with ThreadPoolExecutor(max_workers=5) as workers:
            gate = workers.submit(request, "gate")
            if not gate_started.wait(timeout=3):
                raise RuntimeError("probe_gate_start")
            futures = [
                workers.submit(request, value) for value in ["0", "1", "bad", "3"]
            ]
            until = time.monotonic() + 2
            while json.loads(client.exchange(StatusRequest()))["queue_depth"] < 4:
                if time.monotonic() >= until:
                    raise RuntimeError("probe_queue_timeout")
                time.sleep(0.005)
            release_gate.set()
            gate.result()
            results = [future.result() for future in futures]
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
        release_gate.set()
        stop.set()
        server.join(timeout=5)
        if server.is_alive():
            raise RuntimeError("probe_shutdown")


if __name__ == "__main__":
    main()
