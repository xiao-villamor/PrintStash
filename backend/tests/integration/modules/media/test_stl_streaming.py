"""Legacy streaming cancellation follows the same worker-tree cleanup contract."""

import json
from pathlib import Path

import pytest

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media import stl_streaming
from app.modules.media.worker_bootstrap import command
from tests.factories import content


class TestStreamingCancellation:
    def test_cancellation_reaps_owned_workers(self, tmp_path, monkeypatch):
        source = tmp_path / "part.stl"
        source.write_bytes(content.binary_stl())
        pids = tmp_path / "pids"
        original = stl_streaming.subprocess.Popen
        processes = []

        def waiting(_argv, **kwargs):
            process = original(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree_wait", str(pids)],
                    256 * 1024**2,
                ),
                **kwargs,
            )
            processes.append(process)
            return process

        monkeypatch.setattr(stl_streaming.subprocess, "Popen", waiting)
        with cancellation_scope(pids.exists), pytest.raises(OperationCancelled):
            stl_streaming.render_stl_preview_isolated(source)
        assert processes[0].poll() is not None
        for pid in json.loads(pids.read_text()):
            assert not Path(f"/proc/{pid}").exists()
        assert set(tmp_path.iterdir()) == {source, pids}
