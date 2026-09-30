"""Acceptance checks through the real pre-import bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.modules.media import mesh_isolation
from app.modules.media.thumbnail_engine import ThumbnailFailureReason
from tests.paths import BACKEND_DIR

MB = 1024**2


class TestWorkerBootstrap:
    def test_refuses_sudden_allocation_before_rss_polling(
        self,
    ):
        from app.modules.media.worker_bootstrap import command

        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            mesh_isolation.supervise(
                command("tests.fakes.mesh_bootstrap_probe", ["burst"], 128 * MB),
                memory_budget=128 * MB,
                timeout_seconds=10,
            )
        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

    def test_native_dependencies_start_under_hard_ceiling(
        self,
    ):
        from app.modules.media.worker_bootstrap import command

        result = mesh_isolation.supervise(
            command("tests.fakes.mesh_bootstrap_probe", ["native"], 1024 * MB),
            memory_budget=1024 * MB,
            timeout_seconds=30,
        )
        assert int(result) == 1024 * MB

    def test_worker_dies_with_its_parent(self, tmp_path):
        pid_file = tmp_path / "pid"
        script = (
            "import subprocess,time; "
            "from app.modules.media.worker_bootstrap import command; "
            f"subprocess.Popen(command('tests.fakes.mesh_bootstrap_probe', ['wait', {str(pid_file)!r}], 134217728)); "
            "time.sleep(60)"
        )
        parent = subprocess.Popen([sys.executable, "-c", script], cwd=BACKEND_DIR)
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert pid_file.exists()
            child_pid = int(pid_file.read_text())
            parent.kill()
            parent.wait()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = Path(f"/proc/{child_pid}/stat")
                if not status.exists() or status.read_text().split()[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                os.kill(child_pid, 9)
                pytest.fail("worker survived its parent")
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait()

    def test_counts_descendants_against_the_admitted_budget(
        self,
    ):
        from app.modules.media.worker_bootstrap import command

        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            mesh_isolation.supervise(
                command("tests.fakes.mesh_bootstrap_probe", ["tree"], 256 * MB),
                memory_budget=160 * MB,
                timeout_seconds=10,
            )
        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
