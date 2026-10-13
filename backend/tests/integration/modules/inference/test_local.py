"""Preplaced native ONNX runs in a bounded child; failed assets never reach a vector store."""

import json
from dataclasses import replace

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput

from app.db.session import get_session_factory
from app.modules.inference import local
from app.modules.inference.local import LocalEmbeddingProvider
from tests.factories.embeddings import local_embedding_assets


@pytest.fixture
def assets(tmp_path):
    return local_embedding_assets(tmp_path / "assets")


def _hold_every_slot() -> int:
    """Occupy this process's inference admission, as busy renders would."""
    from printstash_core.inference.context import InferenceContext

    from app.core.config import settings

    held = max(int(settings.max_render_jobs), 1)
    for _ in range(held):
        local.acquire_slot(InferenceContext.bounded(1, priority="background"))
    return held


def _release(count: int) -> None:
    for _ in range(count):
        local.release_slot()


class TestLocalProvider:
    @pytest.mark.parametrize("threads", [0, 5], ids=["zero", "over-cap"])
    def test_refuses_invalid_local_thread_budgets(self, db_session, assets, threads):
        with pytest.raises(EmbeddingError, match="embedding_thread_budget_invalid"):
            LocalEmbeddingProvider(
                get_session_factory(), assets, "two-tower-contract", threads
            )

    @pytest.mark.parametrize(
        "mode, code",
        [
            ("json", "embedding_output_invalid"),
            ("identity", "embedding_output_mismatch"),
            ("vectors", "embedding_output_mismatch"),
            ("truncations", "embedding_output_mismatch"),
            ("oversized", "embedding_output_budget"),
            ("trailing", "embedding_output_invalid"),
            ("exit", "embedding_inference_failed"),
        ],
    )
    def test_rejects_malformed_native_worker_replies(
        self, db_session, assets, monkeypatch, mode, code
    ):
        import subprocess
        import sys

        from app.modules.inference.worker_pool import pool
        from tests.fakes import faulty_embedding_worker

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        monkeypatch.setattr(
            provider,
            "_spawn",
            lambda **_kwargs: subprocess.Popen(
                [sys.executable, faulty_embedding_worker.__file__, mode],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ),
        )

        try:
            with pytest.raises(EmbeddingError, match=code):
                provider.embed((EmbeddingInput("text", text="red"),), provider.space)
        finally:
            pool.close()

        # A failed exchange still hands its admission back.
        assert local._running == 0

    def test_yields_background_admission_to_waiting_queries(self, db_session, assets):
        import time
        from concurrent.futures import ThreadPoolExecutor

        from printstash_core.inference.context import InferenceContext

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        provider.validate()
        held = _hold_every_slot()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                provider.embed,
                (EmbeddingInput("text", text="red"),),
                provider.space,
                context=InferenceContext.bounded(5),
            )
            try:
                deadline = time.monotonic() + 3
                while local._waiting_queries == 0:
                    assert time.monotonic() < deadline, "the query never waited"
                    time.sleep(0.01)
                # A waiting query goes first, even once a slot frees. Freeing
                # it and asking for background admission under the admission
                # lock (re-entrant) keeps the query from taking the slot and
                # finishing in between, which would make this a race.
                with local._admission:
                    _release(1)
                    held -= 1
                    assert local._waiting_queries == 1
                    with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                        local.acquire_slot(
                            InferenceContext.bounded(1, priority="background")
                        )
            finally:
                _release(held)
            assert result.result(3) == ((1, 0, 0),)
        # Completed waiters cannot starve the next background batch.
        assert provider.embed(
            (EmbeddingInput("text", text="red"),),
            provider.space,
            context=InferenceContext.bounded(3, priority="background"),
        ) == ((1, 0, 0),)

    def test_waits_for_local_compute_within_the_query_deadline(
        self, db_session, assets
    ):
        import threading

        from printstash_core.inference.context import InferenceContext

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        provider.validate()
        held = _hold_every_slot()
        timer = threading.Timer(0.15, _release, args=(held,))
        timer.start()
        try:
            assert provider.embed(
                (EmbeddingInput("text", text="red"),),
                provider.space,
                context=InferenceContext.bounded(3),
            ) == ((1, 0, 0),)
        finally:
            timer.join()

    def test_times_out_while_waiting_for_local_compute(self, db_session, assets):
        from printstash_core.inference.context import InferenceContext

        held = _hold_every_slot()
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        try:
            with pytest.raises(EmbeddingError, match="inference_timeout"):
                provider.embed(
                    (EmbeddingInput("text", text="red"),),
                    provider.space,
                    context=InferenceContext.bounded(0.05),
                )
        finally:
            _release(held)

    def test_accepts_read_only_offline_models(self, db_session, assets, monkeypatch):
        import os

        def read_only(*args, **kwargs):
            raise PermissionError("read-only model volume")

        monkeypatch.setattr(os, "utime", read_only)
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)

    @pytest.mark.parametrize("modality", ["text", "image"])
    def test_keeps_local_query_inputs_in_memory(
        self, db_session, assets, monkeypatch, modality
    ):
        import tempfile

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        value = (
            EmbeddingInput("text", text="red")
            if modality == "text"
            else EmbeddingInput("image", rgb=bytes([255, 0, 0]), width=1, height=1)
        )

        def forbid_spooling(*args, **kwargs):
            raise AssertionError("query input must not be spooled to a directory")

        monkeypatch.setattr(tempfile, "TemporaryDirectory", forbid_spooling)
        assert provider.embed((value,), provider.space) == ((1, 0, 0),)

    def test_queries_both_native_towers(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        vectors = provider.embed(
            (
                EmbeddingInput("text", text="red"),
                EmbeddingInput("image", rgb=bytes([255, 0, 0]), width=1, height=1),
            ),
            provider.space,
        )
        np.testing.assert_allclose(vectors, [[1, 0, 0], [1, 0, 0]])

    def test_verifies_canaries_before_availability(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        assert provider.validate().family == "clip"

    def test_refuses_changed_asset(self, db_session, assets):
        with (assets / "image.onnx").open("ab") as stream:
            stream.write(b"changed")
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="asset_digest_mismatch"):
            provider.validate()

    def test_refuses_wrong_canary(self, db_session, assets):
        manifest = json.loads((assets / "manifest.json").read_text())
        manifest["image"]["canary"] = [1, 0, 0]
        (assets / "manifest.json").write_text(json.dumps(manifest))
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="canary_mismatch"):
            provider.validate()

    def test_refuses_tensor_signature_mismatch(self, db_session, assets):
        manifest = json.loads((assets / "manifest.json").read_text())
        manifest["image"]["input_name"] = "wrong"
        (assets / "manifest.json").write_text(json.dumps(manifest))
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="signature_mismatch"):
            provider.validate()

    def test_dino_does_not_advertise_text(self, db_session, tmp_path):
        directory = local_embedding_assets(tmp_path / "dino", family="dino")
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "two-tower-contract", 1
        )
        assert provider.space.modality == "image"
        with pytest.raises(EmbeddingError, match="text_unavailable"):
            provider.embed((EmbeddingInput("text", text="red"),), provider.space)

    def test_refuses_incompatible_space(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="space_mismatch"):
            provider.embed(
                (EmbeddingInput("text", text="red"),),
                replace(provider.space, dimension=4),
            )

    def test_refuses_oversized_batch(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="batch_budget"):
            provider.embed((EmbeddingInput("text", text="red"),) * 9, provider.space)

    def test_contains_worker_memory_limit(self, db_session, assets, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "embedding_worker_memory_mb", 1)
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="worker_oom"):
            provider.validate()

    def test_contains_worker_deadline(self, db_session, assets, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "mesh_step_timeout_seconds", 0.00001)
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="embedding_timeout"):
            provider.validate()

    def test_reports_configured_capability(self, db_session, assets, monkeypatch):
        from app.core.config import _overlay
        from app.modules.inference.local import configured_provider

        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        monkeypatch.setitem(_overlay, "embedding_model_key", "two-tower-contract")
        with pytest.raises(EmbeddingError, match="embedding_not_configured"):
            configured_provider(get_session_factory())
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(assets))
        provider = configured_provider(get_session_factory())
        validated = provider.validate()
        assert validated.space() == provider.space
        assert provider.space.dimension == 3


class TestLocalBoundaries:
    def test_refuses_missing_model_asset(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        graph = assets / "image.onnx"
        original = graph.read_bytes()
        graph.unlink()
        try:
            with pytest.raises(EmbeddingError, match="embedding_asset_unavailable"):
                provider.embed((EmbeddingInput("text", text="red"),), provider.space)
        finally:
            graph.write_bytes(original)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)
        assert graph.read_bytes() == original

    def test_refuses_empty_local_batch(self, db_session, assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="embedding_batch_budget"):
            provider.embed((), provider.space)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)

    def test_reports_unavailable_native_runtime(self, db_session, assets, monkeypatch):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        graph = assets / "image.onnx"
        original = graph.read_bytes()
        discovery = local.importlib.util.find_spec

        def without_runtime(name, *args, **kwargs):
            return None if name == "onnxruntime" else discovery(name, *args, **kwargs)

        monkeypatch.setattr(local.importlib.util, "find_spec", without_runtime)
        with pytest.raises(EmbeddingError, match="embedding_runtime_unavailable"):
            provider.embed((EmbeddingInput("text", text="red"),), provider.space)
        assert graph.read_bytes() == original

    def test_refuses_request_over_actual_input_budget(self, db_session, assets):
        from app.modules.inference.worker_protocol import MAX_INPUT_BYTES

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="embedding_input_budget"):
            provider._request(b"x" * (MAX_INPUT_BYTES + 1))
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)

    @pytest.mark.parametrize("failure", [OSError, ValueError], ids=["os", "value"])
    def test_normalizes_native_launch_failure(
        self, db_session, assets, monkeypatch, failure
    ):
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )

        def unavailable(*args, **kwargs):
            raise failure("private-native-launch-detail")

        with monkeypatch.context() as launch:
            launch.setattr(local.subprocess, "Popen", unavailable)
            with pytest.raises(
                EmbeddingError, match="embedding_inference_failed"
            ) as error:
                provider.embed((EmbeddingInput("text", text="red"),), provider.space)
            assert "private-native-launch-detail" not in str(error.value)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)

    def test_serves_cached_model_when_recency_touch_is_read_only(
        self, db_session, assets, monkeypatch
    ):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "embedding_cache_dir", assets.parent)
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        snapshot = {path.name: path.read_bytes() for path in assets.iterdir()}
        timestamp = assets.stat().st_mtime_ns

        def read_only(path, *args, **kwargs):
            assert path == assets
            raise PermissionError("read-only model cache")

        monkeypatch.setattr(local.os, "utime", read_only)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1, 0, 0),)
        assert assets.stat().st_mtime_ns == timestamp
        assert {path.name: path.read_bytes() for path in assets.iterdir()} == snapshot
