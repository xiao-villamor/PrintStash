"""Independent worker processes share capacity through the real mesh pipeline.

These flows compose the native runtime and execute the actual mesh bootstrap,
reader and measurement transport. Database scheduling is covered by the Job
engine flows; this test proves the resource boundary shared by their executors.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time

import pytest

from app.bootstrap.native_resources import configure
from app.core.config import _overlay
from app.modules.media.native_process import native_capacity
from app.runtime.native_admission import Resources
from app.runtime.native_runtime import admit
from tests.factories import content
from tests.paths import BACKEND_DIR


@pytest.fixture
def workers(tmp_path):
    source = tmp_path / "mesh.stl"
    source.write_bytes(content.binary_stl(triangles=2))
    processes = []

    def start(*, selected_source=None, ready=None, release=None, capacity_bytes=None):
        arguments = ["--source", str(selected_source or source)]
        if ready is not None:
            arguments += ["--ready", str(ready), "--release", str(release)]
        if capacity_bytes is not None:
            arguments += ["--capacity-bytes", str(capacity_bytes)]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.native_admission_process",
                "render",
                str(tmp_path),
                *arguments,
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
            except FileNotFoundError, json.JSONDecodeError:
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

    @pytest.mark.parametrize("_linux", [True] if sys.platform == "linux" else [])
    def test_mixed_stl_workers_share_weighted_capacity(self, tmp_path, workers, _linux):
        start, _queued = workers
        counts = (2, 300000)
        sources = tuple(tmp_path / f"mixed-{count}.stl" for count in counts)
        for source, count in zip(sources, counts, strict=True):
            source.write_bytes(content.binary_stl(triangles=count))
        original_hashes = [
            hashlib.sha256(source.read_bytes()).digest() for source in sources
        ]
        ready = tuple(tmp_path / f"ready-{index}.json" for index in range(2))
        release = tmp_path / "release-geometry"
        capacity = Resources(2, 2 * 1024**3)
        processes = [
            start(
                selected_source=source,
                ready=marker,
                release=release,
                capacity_bytes=capacity.bytes,
            )
            for source, marker in zip(sources, ready, strict=True)
        ]
        try:
            deadline = time.monotonic() + 30
            while not all(marker.exists() for marker in ready):
                assert all(process.poll() is None for process in processes), (
                    "real worker exited before its geometry gate"
                )
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "mixed real workers did not overlap at their geometry gates"
                    )
                time.sleep(0.01)
            observed = [json.loads(marker.read_text()) for marker in ready]
            assert len({item["pid"] for item in observed}) == 2
            assert len({item["identity"] for item in observed}) == 2
            for item in observed:
                os.kill(item["pid"], 0)
            expected_bytes = [512 * 1024**2, 300000 * 3000]
            assert [item["bytes"] for item in observed] == expected_bytes
            records = [
                json.loads(path.read_text())
                for path in (tmp_path / "runtime" / "native").glob("*.ticket")
            ]
            active = [record for record in records if record["state"] == "active"]
            assert {record["name"] for record in active} == {
                item["identity"] for item in observed
            }
            assert sorted(record["request"][1] for record in active) == sorted(
                expected_bytes
            )
            assert sum(record["request"][0] for record in active) <= capacity.slots
            assert sum(record["request"][1] for record in active) <= capacity.bytes
            assert all(
                record["capacity"] == [capacity.slots, capacity.bytes]
                for record in active
            )
        finally:
            release.write_text("release", encoding="utf-8")
        peaks = []
        for process, source, count, original, budget in zip(
            processes, sources, counts, original_hashes, expected_bytes, strict=True
        ):
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, error
            result = json.loads(output.splitlines()[-1])
            assert result["geometry"]["triangle_count"] == count, result
            assert all(
                math.isfinite(result["geometry"][f"bbox_{axis}_mm"]) for axis in "xyz"
            )
            peak = result["peak_tree_rss_bytes"]
            assert isinstance(peak, int) and 0 < peak <= budget
            peaks.append(peak)
            completed = json.loads(ready[sources.index(source)].read_text())
            assert completed["stage"] == "completed"
            assert 0 < completed["completed_peak_virtual_bytes"] <= budget
            assert result["image_bytes"] > 0
            assert result["image_is_png"] is True
            assert hashlib.sha256(source.read_bytes()).digest() == original
        assert sum(peaks) <= capacity.bytes
