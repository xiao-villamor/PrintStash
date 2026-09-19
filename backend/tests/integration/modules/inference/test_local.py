"""Preplaced native ONNX runs in a bounded child; failed assets never reach a vector store."""

import json
from dataclasses import replace

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput
from sqlmodel import select

from app.db.models import ThumbnailRenderSlot
from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from tests.factories.embeddings import local_embedding_assets


@pytest.fixture
def assets(tmp_path):
    return local_embedding_assets(tmp_path / "assets")


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
            lambda: subprocess.Popen(
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

        db_session.expire_all()
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )

    def test_yields_background_admission_to_waiting_queries(
        self, db_session, assets, monkeypatch
    ):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from printstash_core.inference.context import InferenceContext

        from app.core.config import settings
        from app.modules.media import compute_slots

        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        provider.validate()
        slots = [
            compute_slots.acquire(db_session, f"render-{index}")
            for index in range(settings.max_render_jobs)
        ]
        waiting, proceed = threading.Event(), threading.Event()
        acquire = compute_slots.acquire

        def pause_waiter(session, token, **kwargs):
            slot = acquire(session, token, **kwargs)
            if slot is None:
                waiting.set()
                assert proceed.wait(3)
            return slot

        monkeypatch.setattr(compute_slots, "acquire", pause_waiter)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                provider.embed,
                (EmbeddingInput("text", text="red"),),
                provider.space,
                context=InferenceContext.bounded(5),
            )
            try:
                assert waiting.wait(3)
                for index, slot in enumerate(slots):
                    compute_slots.release(db_session, slot.id, f"render-{index}")
                db_session.commit()
                with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                    provider.embed(
                        (EmbeddingInput("text", text="red"),),
                        provider.space,
                        context=InferenceContext.bounded(1, priority="background"),
                    )
            finally:
                proceed.set()
            assert result.result(3) == ((1, 0, 0),)
        # Completed waiters cannot starve the next background batch.
        assert provider.embed(
            (EmbeddingInput("text", text="red"),),
            provider.space,
            context=InferenceContext.bounded(3, priority="background"),
        ) == ((1, 0, 0),)

    def test_waits_for_shared_compute_within_the_query_deadline(
        self, db_session, assets
    ):
        import threading

        from printstash_core.inference.context import InferenceContext

        from app.core.config import settings
        from app.modules.media import compute_slots

        sessions = get_session_factory()
        provider = LocalEmbeddingProvider(sessions, assets, "two-tower-contract", 1)
        provider.validate()
        slots = [
            compute_slots.acquire(db_session, f"render-{index}")
            for index in range(settings.max_render_jobs)
        ]

        def finish_render():
            with sessions.scoped_session() as session:
                for index, slot in enumerate(slots):
                    compute_slots.release(session, slot.id, f"render-{index}")
                session.commit()

        timer = threading.Timer(0.15, finish_render)
        timer.start()
        try:
            assert provider.embed(
                (EmbeddingInput("text", text="red"),),
                provider.space,
                context=InferenceContext.bounded(3),
            ) == ((1, 0, 0),)
        finally:
            timer.join()

    def test_times_out_while_waiting_for_shared_compute(self, db_session, assets):
        from printstash_core.inference.context import InferenceContext

        from app.core.config import settings
        from app.modules.media import compute_slots

        for index in range(settings.max_render_jobs):
            assert compute_slots.acquire(db_session, f"render-{index}") is not None
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="inference_timeout"):
            provider.embed(
                (EmbeddingInput("text", text="red"),),
                provider.space,
                context=InferenceContext.bounded(0.05),
            )

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
        db_session.expire_all()
        assert all(
            slot.lease_token is None
            for slot in db_session.exec(select(ThumbnailRenderSlot)).all()
        )

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

    def test_preserves_render_backpressure(self, db_session, assets):
        from app.core.config import settings
        from app.modules.media import compute_slots

        for index in range(settings.max_render_jobs):
            assert compute_slots.acquire(db_session, f"busy-{index}") is not None
        provider = LocalEmbeddingProvider(
            get_session_factory(), assets, "two-tower-contract", 1
        )
        with pytest.raises(EmbeddingError, match="compute_busy"):
            provider.validate()

    def test_contains_worker_memory_limit(self, db_session, assets, monkeypatch):
        from app.modules.media import compute_slots

        monkeypatch.setattr(compute_slots, "native_memory_budget_bytes", lambda: 1)
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
