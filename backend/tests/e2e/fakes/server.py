"""Run a fake (Starlette) app on a real loopback socket in a background thread.

The fakes must be reachable over a real TCP socket because the point of the E2E
layer is to exercise the app's *real* outbound HTTP stack (httpx, TLS-less but
real sockets, real headers) — not a mocked client. uvicorn runs in a daemon
thread with its own event loop, independent of the test's asyncio loop.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

import uvicorn
from starlette.applications import Starlette


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class RunningServer:
    base_url: str
    port: int
    _server: uvicorn.Server
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def start_server(app: Starlette) -> RunningServer:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for uvicorn to bind before handing the URL to the test.
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("fake server failed to start within 10s")
        time.sleep(0.02)
    return RunningServer(base_url=f"http://127.0.0.1:{port}", port=port, _server=server, _thread=thread)
