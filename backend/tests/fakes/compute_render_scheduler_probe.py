"""Real render IPC grouping; the execution dependency injects bounded faults."""

import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.core.config import _overlay
from app.runtime.compute import client
from app.runtime.compute.broker import serve
from app.runtime.compute.contracts import ComputeMode, ComputeUnavailable, Reason
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import Priority, RenderRequest


def main():
    _overlay["compute_render_policy"] = "preview"
    staging = len(sys.argv) > 2 and sys.argv[2] == "staging"
    if staging:
        from app.runtime.compute import broker
        from app.runtime.compute.budget import QueueBudget

        broker.QueueBudget = lambda _: QueueBudget(4096)
    client.bind(Path(sys.argv[1]))
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

        def execute_render_batch(self, requests):
            batches.append(len(requests))
            if any(r.request_id == "bad" for r in requests):
                raise ValueError("invalid_member")
            if any(r.request_id == "capacity" for r in requests):
                raise ComputeUnavailable(Reason.CAPACITY)
            return [base64.b64decode(r.payload) for r in requests]

        def execute(self, request):
            if request.request_id == "bad":
                raise ComputeUnavailable(Reason.INVALID_INPUT)
            return base64.b64decode(request.payload)

    thread = threading.Thread(
        target=serve, args=(address,), kwargs={"dispatcher_factory": Executor}
    )
    thread.start()
    until = time.monotonic() + 5
    while not (address / "broker.sock").exists():
        if time.monotonic() > until:
            raise RuntimeError("probe_startup")
        time.sleep(0.005)

    def request(value, barrier=None):
        if barrier is not None:
            barrier.wait(timeout=2)
        response = json.loads(
            client.exchange(
                RenderRequest(
                    deadline=time.monotonic() + 3,
                    priority=Priority.BACKGROUND,
                    request_id=value[:128],
                    payload=base64.b64encode(value.encode()).decode(),
                    recipe="contract",
                    units=1,
                )
            )
        )
        return (
            response
            if "error" in response
            else base64.b64decode(response["result"]).decode()
        )

    try:
        if staging:
            try:
                request("x" * 10_000)
                raise AssertionError("over-budget body was admitted")
            except ComputeUnavailable as exc:
                refusal = exc.reason.value
            print(
                json.dumps(
                    {
                        "refusal": refusal,
                        "healthy": request("healthy"),
                        "cooldown": client._disabled_until,
                    }
                )
            )
            return
        results = []
        for values in (
            ["0", "1", "2", "3"],
            ["4", "bad", "5", "6"],
            ["7", "capacity", "8", "9"],
        ):
            barrier = threading.Barrier(4)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results.append(
                    list(
                        pool.map(
                            lambda value, barrier=barrier: request(value, barrier),
                            values,
                        )
                    )
                )
        started = time.monotonic()
        single = request("single")
        print(
            json.dumps(
                {
                    "results": results,
                    "batches": batches,
                    "singleton": single,
                    "singleton_seconds": time.monotonic() - started,
                }
            )
        )
    finally:
        stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("probe_shutdown")


if __name__ == "__main__":
    main()
