"""Real binary transport against an injected native executor, no GPU required."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import trimesh
from printstash_core.mesh.render_geometry import prepare_mesh_render

from app.modules.media.compute_geometry import encode, split
from app.runtime.compute import client
from app.runtime.compute.broker import serve
from app.runtime.compute.contracts import ComputeMode, ComputeUnavailable
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import BinaryRenderRequest, Priority


def main():
    client.bind(Path(sys.argv[1]))
    address = client.directory()
    stop = threading.Event()
    owners = []

    class Executor(Dispatcher):
        def __init__(self, root, **kwargs):
            super().__init__(
                root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
            )
            owners.append(self)

        def admit_host(self, deadline):
            self.host_capacity = 1024**3

        def pressure(self):
            return stop.is_set()

        def execute(self, request):
            assert request._decoded is not None
            return request.request_id.encode()

        def execute_render_batch(self, requests):
            return [self.execute(r) for r in requests]

    thread = threading.Thread(
        target=serve, args=(address,), kwargs={"dispatcher_factory": Executor}
    )
    thread.start()
    until = time.monotonic() + 5
    while not (address / "broker.sock").exists():
        if time.monotonic() > until:
            raise RuntimeError("startup")
        time.sleep(0.005)
    prepared = prepare_mesh_render(trimesh.creation.box())

    def request(name, view=None, corrupt=False, header_override=None):
        key, header, body = split(encode(prepared, 64, 48, [view], False))
        if corrupt:
            key = "0" * 64
        if header_override is not None:
            header = header_override
        request = BinaryRenderRequest(
            deadline=time.monotonic() + 5,
            priority=Priority.BACKGROUND,
            request_id=name,
            recipe="contract",
            units=prepared.face_count,
            geometry_key=key,
            geometry_bytes=len(body),
            header=header,
        )
        try:
            return client.exchange_render(request, body).decode()
        except ComputeUnavailable as exc:
            return exc.reason.value

    try:
        first = request("first")
        before = owners[0].geometry_cache.transferred
        second = request("second", np.eye(3))
        delta = owners[0].geometry_cache.transferred - before
        invalid = [
            request("bad", corrupt=True),
            request("bad-header", header_override="null"),
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(request, ["one", "two", "three", "four"]))
        from app.runtime.compute.geometry_cache import GeometryCache

        owners[0].geometry_cache = GeometryCache(1)
        capacity = request("too-large")
        owners[0].geometry_cache = GeometryCache()
        recovered = request("recovered")
        reuploaded = owners[0].geometry_cache.transferred
        print(
            json.dumps(
                {
                    "capacity": capacity,
                    "recovered": recovered,
                    "reuploaded": reuploaded,
                    "first": first,
                    "second": second,
                    "uploaded": before,
                    "delta": delta,
                    "invalid": invalid,
                    "outputs": outputs,
                    "healthy": request("healthy"),
                }
            )
        )
    finally:
        stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("shutdown")


if __name__ == "__main__":
    main()
