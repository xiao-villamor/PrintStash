"""Disposable broker owner for native failure and ready-queue contracts."""

import base64
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.core.config import _overlay
from app.runtime.compute import broker, client
from app.runtime.compute.contracts import ComputeMode
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import InferenceRequest, Priority


def main():
    scenario = sys.argv[2]
    root = Path(sys.argv[1])
    client.bind(root)
    address = client.directory()
    stop, collecting = threading.Event(), threading.Event()
    order = []
    _overlay["compute_batch_wait_ms"] = (
        1000 if scenario in {"preempt", "withdraw"} else 0
    )
    filled, refused = threading.Event(), threading.Event()
    slot_state = {"accepted": 0, "released": 0}
    slot_lock = threading.Lock()
    original_slots = threading.BoundedSemaphore

    class ControlledSlots(original_slots):
        def acquire(self, *args, **kwargs):
            accepted = super().acquire(*args, **kwargs)
            if accepted:
                with slot_lock:
                    slot_state["accepted"] += 1
                    if slot_state["accepted"] == 16:
                        filled.set()
            else:
                refused.set()
            return accepted

        def release(self, *args, **kwargs):
            with slot_lock:
                slot_state["released"] += 1
            return super().release(*args, **kwargs)

    if scenario == "slots":
        broker.threading.BoundedSemaphore = ControlledSlots
    original_queue = queue.Queue

    class ReadyQueue(original_queue):
        def get(self, block=True, timeout=None):
            if (
                threading.current_thread().name == "compute-device"
                and timeout is not None
                and timeout > 0.1
            ):
                collecting.set()
            return super().get(block, timeout)

    if scenario in {"preempt", "withdraw"}:
        broker.queue.Queue = ReadyQueue
    if scenario == "rss":
        broker.process_rss_bytes = lambda pid: 8 * 1024**3

    class NativeFault(Exception):
        pass

    class Executor(Dispatcher):
        def __init__(self, root, **kwargs):
            super().__init__(
                root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
            )
            if scenario == "deadline":
                self.active_deadline = time.monotonic() - 1

        def pressure(self):
            return stop.is_set()

        def execute(self, request):
            order.append(request.request_id)
            if scenario == "memory":
                raise MemoryError()
            if scenario == "plugin":
                raise NativeFault("private native failure")
            return b"result"

    if scenario in {"rss", "deadline"}:
        broker.serve(address, dispatcher_factory=Executor)
        raise AssertionError("watchdog did not exit")
    server = threading.Thread(
        target=broker.serve, args=(address,), kwargs={"dispatcher_factory": Executor}
    )
    server.start()
    try:
        until = time.monotonic() + 5
        while not (address / "broker.sock").exists():
            if time.monotonic() >= until:
                raise RuntimeError("startup timeout")
            time.sleep(0.005)

        if scenario == "slots":
            connections = []
            try:
                for _ in range(16):
                    connections.append(
                        client._connect(address, time.monotonic() + 2, start=False)
                    )
                assert filled.wait(2)
                with client._connect(
                    address, time.monotonic() + 2, start=False
                ) as extra:
                    assert refused.wait(2)
                    extra.settimeout(2)
                    assert extra.recv(1) == b""
            finally:
                for connection in connections:
                    connection.close()
            stop.set()
            server.join(6)
            assert not server.is_alive()
            print(json.dumps(slot_state))
            return

        def request(name, priority):
            message = InferenceRequest(
                deadline=time.monotonic() + (0.1 if name == "withdrawn" else 4),
                priority=priority,
                request_id=name,
                directory="/models",
                model_key="test",
                threads=1,
                payload=base64.b64encode(
                    json.dumps(
                        {
                            "config_hash": "a" * 64,
                            "inputs": [{"modality": "text", "text": name}],
                        }
                    ).encode()
                ).decode(),
            )
            from app.runtime.compute.contracts import ComputeUnavailable

            try:
                return json.loads(client.exchange(message))
            except ComputeUnavailable as exc:
                return {"error": exc.reason.value}

        if scenario in {"preempt", "withdraw"}:
            with ThreadPoolExecutor(max_workers=2) as pool:
                background = pool.submit(request, "background", Priority.BACKGROUND)
                assert collecting.wait(2)
                interactive = pool.submit(
                    request,
                    "interactive" if scenario == "preempt" else "withdrawn",
                    Priority.INTERACTIVE
                    if scenario == "preempt"
                    else Priority.BACKGROUND,
                )
                responses = [background.result(), interactive.result()]
        else:
            responses = [request("fault", Priority.INTERACTIVE)]
        print(
            json.dumps(
                {
                    "order": order,
                    "responses": responses,
                    "cooled": (address / "cooldown").exists(),
                }
            ),
            flush=True,
        )
    finally:
        stop.set()
        server.join(6)
        assert not server.is_alive()
        broker.queue.Queue = original_queue
        broker.threading.BoundedSemaphore = original_slots


if __name__ == "__main__":
    main()
