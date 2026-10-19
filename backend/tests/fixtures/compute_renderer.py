"""Qualified external renderer with real broker transport and no GPU dependency."""

import multiprocessing
import os
import time

import pytest

from app.core.config import _overlay
from app.runtime.compute import client
from tests.fakes.qualified_batch_renderer import QualifiedBatchRenderer, serve_qualified


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
    # The shipped broker samples its own RSS and exits its entire process on a
    # hard budget violation. A thread would instead sample/terminate pytest,
    # whose accumulated suite state is unrelated to the controlled renderer.
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        controls = QualifiedBatchRenderer(manager)
        owner = context.Process(
            target=serve_qualified,
            args=(root, controls, dict(_overlay)),
            name="qualified-renderer",
        )
        owner.start()
        try:
            deadline = time.monotonic() + 30
            while not (root / "broker.sock").exists():
                assert owner.is_alive(), f"test broker exited: {owner.exitcode}"
                assert time.monotonic() < deadline, "test broker did not start"
                time.sleep(0.01)
            yield controls
        finally:
            controls.release_owner.set()
            controls.release_batch.set()
            controls.stop.set()
            owner.join(timeout=10)
            if owner.is_alive():
                owner.kill()
                owner.join(timeout=5)
            assert owner.exitcode == 0, f"test broker exited: {owner.exitcode}"
