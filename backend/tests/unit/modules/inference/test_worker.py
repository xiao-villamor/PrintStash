"""Persistent native framing sanitizes faults without hiding memory exhaustion."""

import json
import struct
from io import BytesIO
from unittest.mock import Mock

import pytest
from printstash_core.inference import EmbeddingError

from app.modules.inference import worker


class TestPersistentFaults:
    @pytest.mark.parametrize(
        "failure,code",
        [
            (EmbeddingError("embedding_input_invalid"), "embedding_input_invalid"),
            (ValueError("private native details"), "embedding_inference_failed"),
        ],
    )
    def test_returns_sanitized_failure_frame(self, monkeypatch, failure, code):
        monkeypatch.setattr(worker.sys, "argv", ["worker", "/assets", "model", "1"])
        native = Mock()
        native.execute.side_effect = failure
        monkeypatch.setattr(worker, "NativeWorker", Mock(return_value=native))
        output = BytesIO()
        assert worker.serve(BytesIO(struct.pack("!I", 2) + b"{}"), output) == 0
        payload = output.getvalue()
        assert struct.unpack("!I", payload[:4])[0] == len(payload) - 4
        assert json.loads(payload[4:]) == {"code": code}

    def test_preserves_memory_failure_for_supervision(self, monkeypatch):
        monkeypatch.setattr(worker.sys, "argv", ["worker", "/assets", "model", "1"])
        native = Mock()
        native.execute.side_effect = MemoryError()
        monkeypatch.setattr(worker, "NativeWorker", Mock(return_value=native))
        output = BytesIO()
        with pytest.raises(MemoryError):
            worker.serve(BytesIO(struct.pack("!I", 2) + b"{}"), output)
        assert output.getvalue() == b""

    def test_retires_idle_worker_under_shared_pressure(self, monkeypatch):
        monkeypatch.setattr(worker.sys, "argv", ["worker", "/assets", "model", "1"])
        native = Mock()
        native.execute.return_value = b"{}"
        monkeypatch.setattr(worker, "NativeWorker", Mock(return_value=native))
        monkeypatch.setattr(worker.select, "select", lambda *args: ([], [], []))
        assert (
            worker.serve(
                BytesIO(struct.pack("!I", 2) + b"{}"),
                BytesIO(),
                idle_pressure=lambda: True,
            )
            == worker.RETIREMENT_EXIT
        )
