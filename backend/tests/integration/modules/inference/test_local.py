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
