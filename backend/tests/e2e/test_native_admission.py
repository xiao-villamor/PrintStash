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
from pathlib import Path

import pytest

from app.bootstrap.native_resources import configure
from app.core.config import _overlay
from app.modules.media.native_process import native_capacity
from app.runtime.native_admission import LocalResourcePool, Resources
from app.runtime.native_runtime import admit
from tests.factories import content
from tests.paths import BACKEND_DIR


@pytest.fixture
def workers(tmp_path):
    source = tmp_path / "mesh.stl"
    source.write_bytes(content.binary_stl(triangles=2))
    processes = []

    def start(
        *,
        selected_source=None,
        ready=None,
        release=None,
        capacity_bytes=None,
        cancel=None,
        gate_timeout=None,
    ):
        arguments = ["--source", str(selected_source or source)]
        if ready is not None:
            arguments += ["--ready", str(ready), "--release", str(release)]
        if capacity_bytes is not None:
            arguments += ["--capacity-bytes", str(capacity_bytes)]
        if cancel is not None:
            arguments += ["--cancel", str(cancel)]
        if gate_timeout is not None:
            arguments += ["--gate-timeout", str(gate_timeout)]
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


@pytest.fixture
def geometry_gate():
    def wait(process, marker):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output, errors = process.communicate(timeout=5)
                raise AssertionError(
                    "worker exited before its geometry gate: " + output + errors
                )
            try:
                record = json.loads(marker.read_text())
            except FileNotFoundError, json.JSONDecodeError:
                time.sleep(0.01)
                continue
            if record["stage"] == "geometry":
                return record
            raise AssertionError("worker passed its unreleased geometry gate")
        raise TimeoutError("worker did not reach its geometry gate")

    return wait


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

    @pytest.mark.parametrize("_linux", [True] if sys.platform == "linux" else [])
    def test_cancelling_one_worker_preserves_its_peer(
        self, tmp_path, workers, geometry_gate, _linux
    ):
        import fcntl

        start, _queued = workers
        healthy_source = tmp_path / "healthy.stl"
        healthy_bytes = content.binary_stl(triangles=2)
        healthy_source.write_bytes(healthy_bytes)
        cancelled_source = tmp_path / "cancelled.stl"
        cancelled_bytes = content.binary_stl(triangles=3)
        cancelled_source.write_bytes(cancelled_bytes)
        capacity_bytes = 2 * 1024**3
        baseline_release = tmp_path / "baseline-release"
        baseline_release.touch()
        baseline = start(
            selected_source=healthy_source,
            ready=tmp_path / "baseline-ready.json",
            release=baseline_release,
            capacity_bytes=capacity_bytes,
        )
        baseline_output, baseline_errors = baseline.communicate(timeout=30)
        assert baseline.returncode == 0, baseline_errors
        expected = json.loads(baseline_output.splitlines()[-1])
        assert expected["failure_reason"] is None
        assert expected["image_bytes"] > 0

        healthy_ready = tmp_path / "healthy-ready.json"
        cancelled_ready = tmp_path / "cancelled-ready.json"
        healthy_release = tmp_path / "healthy-release"
        cancelled_release = tmp_path / "cancelled-release"
        cancel = tmp_path / "cancel"
        healthy = start(
            selected_source=healthy_source,
            ready=healthy_ready,
            release=healthy_release,
            capacity_bytes=capacity_bytes,
            gate_timeout=60,
        )
        cancelled = start(
            selected_source=cancelled_source,
            ready=cancelled_ready,
            release=cancelled_release,
            capacity_bytes=capacity_bytes,
            cancel=cancel,
            gate_timeout=60,
        )
        pool = LocalResourcePool(tmp_path / "runtime" / "native")
        try:
            healthy_at_gate = geometry_gate(healthy, healthy_ready)
            cancelled_at_gate = geometry_gate(cancelled, cancelled_ready)
            healthy_ticket = Path(healthy_at_gate["ticket"])
            cancelled_ticket = Path(cancelled_at_gate["ticket"])
            healthy_receipt = healthy_ticket.read_bytes()
            assert healthy_at_gate["identity"] != cancelled_at_gate["identity"]
            assert healthy_at_gate["pid"] != cancelled_at_gate["pid"]
            assert cancelled_ticket.exists()

            cancel.touch()
            cancelled_output, cancelled_errors = cancelled.communicate(timeout=15)

            assert cancelled.returncode == 2, cancelled_errors
            assert json.loads(cancelled_output.splitlines()[-1]) == {
                "cancelled": True,
                "exit_cause": "cancelled",
            }
            with pytest.raises(ProcessLookupError):
                os.kill(cancelled_at_gate["pid"], 0)
            assert pool.has_waiters(checkpoint=lambda: None) is False
            assert not cancelled_ticket.exists()
            assert healthy_ticket.read_bytes() == healthy_receipt
            with healthy_ticket.open("r+b") as handle, pytest.raises(BlockingIOError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert healthy.poll() is None
            os.kill(healthy_at_gate["pid"], 0)
        finally:
            healthy_release.touch()
            cancelled_release.touch()
        healthy_output, healthy_errors = healthy.communicate(timeout=30)
        assert healthy.returncode == 0, healthy_errors
        actual = json.loads(healthy_output.splitlines()[-1])
        assert actual["geometry"] == expected["geometry"]
        assert actual["geometry_outcome"] == expected["geometry_outcome"]
        assert actual["volume"] == expected["volume"]
        assert actual["coverage"] == expected["coverage"]
        assert actual["strategy"] == expected["strategy"]
        assert actual["failure_reason"] == expected["failure_reason"]
        assert actual["image_sha256"] == expected["image_sha256"]
        assert actual["image_bytes"] == expected["image_bytes"]
        assert healthy_source.read_bytes() == healthy_bytes
        assert cancelled_source.read_bytes() == cancelled_bytes
        assert pool.has_waiters(checkpoint=lambda: None) is False
        assert list(pool.directory.glob("*.ticket")) == []
