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


class TestRenderPreview:
    @pytest.mark.parametrize(
        "policy,mode,render,expected",
        [
            ("preview", "auto", True, True),
            ("qualified", "auto", True, False),
            ("preview", "cpu", True, False),
            ("preview", "auto", False, False),
        ],
        ids=["preview", "unqualified", "cpu", "inference"],
    )
    def test_requires_explicit_render_evaluation(
        self, tmp_path, monkeypatch, policy, mode, render, expected
    ):
        from app.core.config import _overlay

        _overlay["compute_mode"] = mode
        _overlay["compute_render_policy"] = policy
        monkeypatch.setenv(client.ROOT_ENV, str(tmp_path))
        monkeypatch.setattr(client, "_disabled_until", 0)
        monkeypatch.setattr(client.importlib.util, "find_spec", lambda name: object())
        assert client.available(render=render) is expected


class TestWarmRender:
    def test_cools_failed_owner_startup(self, monkeypatch):
        from app.runtime.compute.contracts import Reason

        monkeypatch.setattr(client, "available", lambda **kwargs: True)
        monkeypatch.setattr(
            client, "status", lambda: client.cpu_status(Reason.DEVICE_FAILED)
        )
        monkeypatch.setattr(client, "_disabled_until", 0)
        client.warm_render()
        assert client._disabled_until > 0

    def test_cpu_deployment_does_not_start_an_owner(self, monkeypatch):
        monkeypatch.setattr(client, "available", lambda **kwargs: False)

        def forbidden():
            pytest.fail("CPU deployment started an optional broker")

        monkeypatch.setattr(client, "status", forbidden)
        assert client.warm_render() is None
