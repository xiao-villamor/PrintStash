"""Exercise actual ready queues and watchdogs with a controllable native renderer."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
from printstash_core.mesh.render_geometry import PreparedRender

from app.modules.media.compute_geometry import encode, split
from app.modules.media.compute_render import RECIPE
from app.runtime.compute import client
from app.runtime.compute.broker import serve
from app.runtime.compute.contracts import ComputeMode, ComputeUnavailable, Reason
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import BinaryRenderRequest, Priority, StatusRequest


def main():
    client.bind(Path(sys.argv[1]))
    address = client.directory()
    scenario = sys.argv[2]
    stop = threading.Event()
    blocked = threading.Event()
    release = threading.Event()
    executing = threading.Event()
    cancelled = threading.Event()
    batches = []

    class Executor(Dispatcher):
        def __init__(self, root, **kwargs):
            super().__init__(
                root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
            )

        def admit_host(self, deadline):
            self.host_capacity = 1024**3

        def pressure(self):
            return stop.is_set()

        def render_many(self, requests):
            names = [request.request_id for request in requests]
            batches.append(names)
            if names == ["blocker"]:
                blocked.set()
                if not release.wait(5):
                    raise RuntimeError("probe_release_timeout")
            elif scenario == "deadline" and "short" in names:
                time.sleep(0.6)
            elif scenario == "cancel" and "cancelled" in names:
                executing.set()
                if not cancelled.wait(5):
                    raise RuntimeError("probe_cancel_timeout")
                time.sleep(0.15)
            return [request.request_id.encode() for request in requests]

    thread = threading.Thread(
        target=serve, args=(address,), kwargs={"dispatcher_factory": Executor}
    )
    thread.start()
    until = time.monotonic() + 5
    while not (address / "broker.sock").exists():
        if time.monotonic() > until:
            raise RuntimeError("probe_startup")
        time.sleep(0.005)

    prepared = PreparedRender(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
        1,
        lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )

    def request(name, frames=1, timeout=4, priority=Priority.BACKGROUND):
        key, header, body = split(encode(prepared, 64, 48, [None] * frames, False))
        message = BinaryRenderRequest(
            deadline=time.monotonic() + timeout,
            priority=priority,
            request_id=name,
            recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:64x48:{frames}:0",
            units=frames,
            geometry_key=key,
            geometry_bytes=len(body),
            header=header,
        )

        def checkpoint():
            if name == "cancelled" and cancelled.is_set():
                raise ComputeUnavailable(Reason.CANCELLED)

        try:
            return client.exchange_render(message, body, checkpoint=checkpoint).decode()
        except ComputeUnavailable as exc:
            return exc.reason.value

    def await_queue(count):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            state = json.loads(client.exchange(StatusRequest()))
            if state["queue_depth"] >= count:
                return
            time.sleep(0.005)
        raise RuntimeError("probe_queue_timeout")

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            blocker = pool.submit(request, "blocker", priority=Priority.INTERACTIVE)
            if not blocked.wait(5):
                raise RuntimeError("probe_block_timeout")
            if scenario == "frames":
                futures = [
                    pool.submit(request, str(index), frames=4) for index in range(3)
                ]
                await_queue(3)
            elif scenario == "deadline":
                futures = [
                    pool.submit(request, "short", timeout=0.3),
                    pool.submit(request, "live"),
                ]
                await_queue(2)
            elif scenario == "interactive":
                background = pool.submit(request, "background")
                await_queue(1)
                interactive = pool.submit(
                    request, "interactive", priority=Priority.INTERACTIVE
                )
                await_queue(2)
                futures = [background, interactive]
            elif scenario == "cancel":
                futures = [
                    pool.submit(request, "cancelled"),
                    pool.submit(request, "live"),
                ]
                await_queue(2)
            else:
                raise ValueError("unknown_probe_scenario")
            release.set()
            if scenario == "cancel":
                if not executing.wait(5):
                    raise RuntimeError("probe_execution_timeout")
                cancelled.set()
            results = [future.result(timeout=5) for future in futures]
            assert blocker.result(timeout=5) == "blocker"
        healthy = request("healthy")
        state = json.loads(client.exchange(StatusRequest()))
        print(
            json.dumps(
                {
                    "results": results,
                    "batches": batches,
                    "healthy": healthy,
                    "interactive": state["render_interactive_requests"],
                    "background": state["render_background_requests"],
                }
            )
        )
    finally:
        release.set()
        cancelled.set()
        stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("probe_shutdown")


if __name__ == "__main__":
    main()
