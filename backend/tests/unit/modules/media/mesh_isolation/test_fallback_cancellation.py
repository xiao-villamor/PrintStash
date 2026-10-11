"""Guarded fallback checks cancellation at launch and clean-reap boundaries."""

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media import mesh_isolation, native_budget
from app.modules.media.thumbnail_handoff import HandoffPlan, ThumbnailHandoff
from app.modules.media.thumbnail_packet import PacketDescriptor
from app.runtime.native_admission import Resources


class TestFallbackCancellation:
    @pytest.mark.parametrize("stage", ["admission", "reap"])
    def test_withholds_withdrawn_fallback_at_phase_boundary(self, monkeypatch, stage):
        monkeypatch.setattr(time, "monotonic", lambda: 10.0)
        amount = Resources(1, 64 * 1024**2)
        monkeypatch.setattr(
            mesh_isolation.native_process, "native_capacity", lambda: amount
        )
        monkeypatch.setattr(native_budget, "request", lambda *a, **k: amount)
        monkeypatch.setattr(
            mesh_isolation, "worker_command", lambda *a: ["guarded-worker"]
        )
        reads = []
        stopped = [False]
        active = []

        def withdrawn():
            reads.append(True)
            return stopped[0]

        @contextmanager
        def admission(*args, checkpoint, **kwargs):
            for _ in range(100):
                checkpoint()
            assert len(reads) == 1
            stopped[0] = stage == "admission"
            active.append(True)
            try:
                yield SimpleNamespace(resources=amount)
            finally:
                active.pop()

        def complete(*args, **kwargs):
            assert active == [True]
            assert kwargs["timeout_seconds"] == 1.0
            stopped[0] = True
            return SimpleNamespace(payload=b"completed-fallback")

        supervised = Mock(side_effect=complete)
        monkeypatch.setattr(mesh_isolation, "admission", admission)
        monkeypatch.setattr(mesh_isolation, "supervise_result", supervised)
        owner = ThumbnailHandoff(
            HandoffPlan(640, 480, amount, amount), Path("workspace"), 11.0
        )
        with cancellation_scope(withdrawn), pytest.raises(OperationCancelled):
            mesh_isolation._render_packet_cpu(
                PacketDescriptor(100, "a" * 64, 1), Path("workspace"), (1, 2), owner
            )
        assert supervised.call_count == (stage == "reap")
        assert len(reads) == (2 if stage == "admission" else 3)
        assert active == []
