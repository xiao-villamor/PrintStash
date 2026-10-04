"""Independent worker processes share capacity through the real mesh pipeline.

These flows compose the native runtime and execute the actual mesh bootstrap,
reader and measurement transport. Database scheduling is covered by the Job
engine flows; this test proves the resource boundary shared by their executors.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from app.bootstrap.native_resources import configure
from app.core.config import _overlay
from app.modules.media.native_process import native_capacity
from app.runtime.native_runtime import admit
from tests.factories import content
from tests.paths import BACKEND_DIR


@pytest.fixture
def workers(tmp_path):
    source = tmp_path / "mesh.stl"
    source.write_bytes(content.binary_stl(triangles=2))
    processes = []

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.native_admission_process",
                "render",
                str(tmp_path),
                "--source",
                str(source),
            ],
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "VAULT_PROCESS_ROLE": "worker",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )
        processes.append(process)
        return process

    def queued():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                records = [
                    json.loads(path.read_text())
                    for path in (tmp_path / "runtime" / "native").glob("*.ticket")
                ]
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.025)
                continue
            if sum(record["state"] == "queued" for record in records) == 2:
                return True
            time.sleep(0.025)
        raise TimeoutError("workers did not reach the shared admission queue")

    try:
        yield start, queued
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


class TestNativeAdmission:
    def test_worker_processes_wait_for_the_same_capacity(
        self, tmp_path, monkeypatch, workers
    ):
        start, queued = workers
        monkeypatch.setitem(_overlay, "max_render_jobs", 2)
        configure(tmp_path)
        capacity = native_capacity()

        with admit(capacity, capacity, checkpoint=lambda: None):
            first, second = start(), start()
            assert queued()
            assert first.poll() is None
            assert second.poll() is None
        first_output, first_error = first.communicate(timeout=30)
        second_output, second_error = second.communicate(timeout=30)

        assert first.returncode == 0, first_error
        assert second.returncode == 0, second_error
        assert (
            json.loads(first_output.splitlines()[-1])["geometry"]["triangle_count"] == 2
        )
        assert (
            json.loads(second_output.splitlines()[-1])["geometry"]["triangle_count"]
            == 2
        )
