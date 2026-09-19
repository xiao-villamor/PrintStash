"""Point exports use the existing contained ONNX provider and exact paired tower."""

import json
from dataclasses import replace

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.points import canary_input
from pydantic import ValidationError

from app.core.config import _overlay
from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import (
    PointModelManifest,
    read_manifest,
    validate_space,
)
from tests.factories.embeddings import point_embedding_assets


@pytest.fixture
def point_assets(tmp_path, monkeypatch):
    path = point_embedding_assets(tmp_path / "cache" / "point")
    monkeypatch.setitem(_overlay, "embedding_cache_dir", path.parent)
    return path


class TestPointManifest:
    @pytest.mark.parametrize(
        "change", ["alignment", "dimension", "paired_asset", "name_collision"]
    )
    def test_rejects_incompatible_point_towers(self, point_assets, change):
        payload = json.loads((point_assets / "manifest.json").read_text())
        if change == "alignment":
            payload["paired_space_hash"] = "f" * 64
        elif change == "dimension":
            payload["point"]["canary"] = [1]
        elif change == "paired_asset":
            payload["paired"]["text"]["graph"]["sha256"] = "f" * 64
        else:
            payload["point"]["graph"]["filename"] = "image.onnx"
        with pytest.raises(
            ValidationError,
            match="embedding_(point_alignment_mismatch|asset_name_collision)",
        ):
            PointModelManifest.model_validate(payload)

    def test_rejects_space_alignment_substitution(self, point_assets):
        manifest = read_manifest(point_assets, "point-contract")
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            validate_space(
                manifest, replace(manifest.space(), alignment_identity="f" * 64)
            )


class TestPointWorker:
    def test_runs_all_paired_modalities_locally(self, db_session, point_assets):
        provider = LocalEmbeddingProvider(
            get_session_factory(), point_assets, "point-contract", 1
        )
        vectors = provider.embed(
            (
                canary_input(),
                EmbeddingInput("text", text="gray"),
                EmbeddingInput("image", rgb=b"\x7f\x7f\x7f", width=1, height=1),
            ),
            provider.space,
            context=InferenceContext.bounded(30),
        )
        assert len(vectors) == 3
        for vector in vectors:
            np.testing.assert_allclose(vector, np.ones(3) / np.sqrt(3), atol=1e-5)
        assert provider.last_truncations == (False, False, False)

    def test_refuses_a_failed_point_canary(self, db_session, point_assets):
        path = point_assets / "manifest.json"
        payload = json.loads(path.read_text())
        payload["point"]["canary"] = [1, 0, 0]
        path.write_text(json.dumps(payload))
        provider = LocalEmbeddingProvider(
            get_session_factory(), point_assets, "point-contract", 1
        )
        with pytest.raises(EmbeddingError, match="embedding_canary_mismatch"):
            provider.validate(context=InferenceContext.bounded(30))
