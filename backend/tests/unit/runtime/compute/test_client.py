"""CPU fallback retains cancellation and the caller's original deadline."""

import json
from pathlib import Path

import pytest
from printstash_core.inference import EmbeddingError, InferenceContext

from app.runtime.compute import client


class TestFallback:
    @pytest.mark.parametrize("reason", ["capacity", "unqualified", "device_failed"])
    def test_returns_valid_work_to_cpu(self, monkeypatch, reason):
        monkeypatch.setattr(client, "available", lambda: True)
        monkeypatch.setattr(
            client,
            "exchange",
            lambda request, **kwargs: json.dumps({"error": reason}).encode(),
        )
        context = InferenceContext.bounded(2)
        deadline = context.deadline

        assert client.infer(Path("/models"), "fixture", 1, b"{}", context) is None
        assert context.deadline == deadline
        assert context.remaining() > 0

    def test_never_turns_cancellation_into_cpu_work(self, monkeypatch):
        monkeypatch.setattr(client, "available", lambda: True)
        context = InferenceContext.bounded(2, cancelled=lambda: True)

        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            client.infer(Path("/models"), "fixture", 1, b"{}", context)
