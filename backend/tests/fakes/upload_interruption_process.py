"""Real application roles for an upload killed before its lease committed.

Starlette parses the whole multipart body before the route runs, so the bytes
reach ``staging/_incoming`` only once ``_stage_upload`` copies them. The stall
role lets that real copy happen, then holds the request before
``_accept_staged`` records the lease; only the parent's SIGKILL ends it. The
startup role is a fresh application process whose owner startup must sweep the
orphan once it is old enough.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import ensure_dirs
from tests.fakes.job_engine_process import _emit, _set_up

STL = (
    b"solid cube\n"
    b"facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
    b"endloop\nendfacet\nendsolid cube\n"
)


def stall() -> None:
    from app.api.v1 import ingest
    from app.main import app

    def held(_session, *, staged: Path, size: int, **_columns):
        _emit(partial=str(staged), bytes=size)
        while True:  # Only the parent's SIGKILL ends this request.
            time.sleep(60)

    ensure_dirs()
    ingest._accept_staged = held  # type: ignore[assignment]
    with TestClient(app) as client:
        _set_up(client)
        client.post(
            "/api/v1/ingest/model",
            files={"file": ("cube.stl", STL, "application/octet-stream")},
        )


def startup(*paths: str) -> None:
    from app.main import app

    # Entering the client runs the lifespan: the owner's startup recovery.
    with TestClient(app):
        pass
    _emit(present={path: Path(path).exists() for path in paths})


if __name__ == "__main__":
    role, *arguments = sys.argv[1:]
    {"stall": stall, "startup": startup}[role](*arguments)
