"""Qualified external renderer with real broker transport and no GPU dependency."""

import contextvars
import os
import threading
import time

import pytest

from app.core.config import _overlay
from app.runtime.compute import client
from app.runtime.compute.broker import serve
from tests.fakes.qualified_batch_renderer import QualifiedBatchRenderer


@pytest.fixture
def qualified_renderer(tmp_path, monkeypatch):
    # Fake the external optional runtime in parent and fresh parser children.
    # Discovery may find it; importing a real GPU is forbidden by this fixture.
    package = tmp_path / "external-runtime"
    package.mkdir()
    (package / "wgpu.py").write_text(
        'raise AssertionError("the test executor owns GPU execution")\n'
    )
    monkeypatch.syspath_prepend(str(package))
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((str(package), os.environ.get("PYTHONPATH", "")))
    )
    monkeypatch.setitem(_overlay, "compute_mode", "auto")
    monkeypatch.setitem(_overlay, "compute_render_policy", "qualified")
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    client.bind(tmp_path)
    root = client.directory()
    assert root is not None
    controls = QualifiedBatchRenderer()
    context = contextvars.copy_context()
    owner = threading.Thread(
        target=context.run,
        args=(serve, root),
        kwargs={"dispatcher_factory": controls.dispatcher},
    )
    owner.start()
    try:
        deadline = time.monotonic() + 5
        while not (root / "broker.sock").exists():
            assert time.monotonic() < deadline, "test broker did not start"
            time.sleep(0.01)
        yield controls
    finally:
        controls.release_owner.set()
        controls.release_batch.set()
        controls.stop.set()
        owner.join(timeout=5)
        assert not owner.is_alive(), "test broker did not stop"
