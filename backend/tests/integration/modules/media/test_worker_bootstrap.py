"""Acceptance checks through the real pre-import bootstrap."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.modules.media import mesh_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
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
            f"subprocess.Popen(command('tests.fakes.mesh_bootstrap_probe', ['wait', {str(pid_file)!r}], 268435456)); "
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

    def test_parent_death_terminates_the_entire_worker_tree(self, tmp_path):
        import json

        pid_file = tmp_path / "tree.json"
        script = (
            "import subprocess,time; "
            "from app.modules.media.worker_bootstrap import command; "
            f"subprocess.Popen(command('tests.fakes.mesh_bootstrap_probe', ['tree_wait', {str(pid_file)!r}], 268435456), start_new_session=True); "
            "time.sleep(60)"
        )
        parent = subprocess.Popen([sys.executable, "-c", script], cwd=BACKEND_DIR)
        pids = []
        try:
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert pid_file.exists()
            pids = json.loads(pid_file.read_text())
            parent.kill()
            parent.wait()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                living = [
                    pid
                    for pid in pids
                    if Path(f"/proc/{pid}/stat").exists()
                    and Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
                ]
                if not living:
                    break
                time.sleep(0.02)
            else:
                pytest.fail(f"worker descendants survived: {living}")
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait()
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass

    def test_success_reaps_descendants_before_returning(self, tmp_path):
        import json

        from app.modules.media.worker_bootstrap import command

        pid_file = tmp_path / "tree.json"
        reply = mesh_isolation.supervise(
            command(
                "tests.fakes.mesh_bootstrap_probe",
                ["leaves_child", str(pid_file)],
                256 * MB,
            ),
            memory_budget=256 * MB,
            timeout_seconds=5,
        )
        assert reply.strip() == b"reply"
        for pid in json.loads(pid_file.read_text()):
            status = Path(f"/proc/{pid}/stat")
            assert not status.exists() or status.read_text().split()[2] == "Z"


class TestAbandonedTemporaryOutputs:
    def test_parent_death_cleans_owned_outputs(self, tmp_path):
        ready = tmp_path / "temporary"
        pids = tmp_path / "pids"
        script = (
            "from app.modules.media.mesh_isolation import supervise; "
            "from app.modules.media.worker_bootstrap import command; "
            f"supervise(command('tests.fakes.mesh_bootstrap_probe', ['tree_wait', {str(pids)!r}, {str(ready)!r}], 268435456), memory_budget=268435456, timeout_seconds=60)"
        )
        parent = subprocess.Popen([sys.executable, "-c", script], cwd=BACKEND_DIR)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
            directory = Path(ready.read_text())
            assert (directory / "partial.stl").exists()
            parent.kill()
            parent.wait()
            deadline = time.monotonic() + 5
            while directory.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not directory.exists()
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait()

    def test_cleanup_preserves_a_replacement_directory(self, tmp_path):
        from app.modules.media.worker_bootstrap import _cleanup_owned_temp

        directory = tmp_path / "printstash-mesh-owned"
        directory.mkdir()
        stat = directory.stat()
        directory.rename(tmp_path / "original")
        directory.mkdir()
        replacement = directory / "replacement"
        replacement.write_bytes(b"other owner")
        _cleanup_owned_temp(directory, (stat.st_dev, stat.st_ino))
        assert replacement.read_bytes() == b"other owner"


class TestDescendantReaping:
    def test_resource_refusal_reaps_adopted_children(self, tmp_path):
        import json

        from app.modules.media.worker_bootstrap import command

        pid_file = tmp_path / "pids"
        with pytest.raises(mesh_isolation.MeshWorkerError):
            mesh_isolation.supervise(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree", str(pid_file), "256"],
                    512 * MB,
                ),
                memory_budget=480 * MB,
                timeout_seconds=10,
            )
        assert pid_file.exists()
        for pid in json.loads(pid_file.read_text()):
            assert not Path(f"/proc/{pid}").exists(), f"unreaped descendant {pid}"


class TestCancelledReply:
    @pytest.mark.parametrize(
        "case", ["leaves_child", "resource_reply"], ids=["success", "resource-refusal"]
    )
    def test_withdrawal_before_reply_acceptance_reaps_descendants(self, tmp_path, case):
        import json

        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.modules.media.worker_bootstrap import command

        pids = tmp_path / "pids"
        with cancellation_scope(pids.exists), pytest.raises(OperationCancelled):
            mesh_isolation.supervise(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    [case, str(pids)],
                    256 * MB,
                ),
                memory_budget=256 * MB,
                timeout_seconds=5,
            )
        for pid in json.loads(pids.read_text()):
            assert not Path(f"/proc/{pid}").exists()


class TestProbeReadiness:
    @pytest.mark.parametrize(
        "case", ["wait", "tree", "tree_wait", "leaves_child", "resource_reply"]
    )
    def test_publishes_complete_markers_before_they_become_visible(
        self, tmp_path, monkeypatch, case
    ):
        import json
        from types import SimpleNamespace

        from tests.fakes import mesh_bootstrap_probe as probe

        pids = tmp_path / "pids"
        ready = tmp_path / "ready"
        temporary = tmp_path / "owned-temporary"
        temporary.mkdir()
        markers = [pids, ready] if case == "tree_wait" else [pids]
        opened = []
        publications = []
        open_file = Path.open
        replace_file = Path.replace

        def observe_open(path, *args, **kwargs):
            stream = open_file(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if "w" in mode and path.parent == tmp_path:
                if any(
                    marker.exists() for marker in markers if marker not in publications
                ):
                    stream.close()
                    pytest.fail(
                        "readiness marker became visible before its write completed"
                    )
                opened.append((path, stream))
            return stream

        def observe_publication(path, target):
            target = Path(target)
            assert target in markers
            assert not target.exists()
            assert path != target
            assert opened[-1][0] == path
            assert opened[-1][1].closed
            content = path.read_text()
            if target == ready:
                assert content == str(temporary)
                assert (
                    temporary / "partial.stl"
                ).read_bytes() == b"partial native output"
            elif case == "wait":
                assert int(content) == os.getpid()
            else:
                assert json.loads(content) == [os.getpid(), 424242]
            result = replace_file(path, target)
            assert target.read_text() == content
            publications.append(target)
            return result

        argv = ["probe", case, str(pids)]
        if case == "tree":
            argv.append("1")
        elif case == "tree_wait":
            argv.append(str(ready))
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.setenv("TMPDIR", str(temporary))
        monkeypatch.setattr(
            subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=424242)
        )
        monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(Path, "open", observe_open)
        monkeypatch.setattr(Path, "replace", observe_publication)

        if case == "resource_reply":
            with pytest.raises(MemoryError, match="resource refusal"):
                probe.main()
        else:
            probe.main()

        assert publications == markers
        assert len(opened) == len(markers)
        assert all(not path.exists() for path, _stream in opened)
