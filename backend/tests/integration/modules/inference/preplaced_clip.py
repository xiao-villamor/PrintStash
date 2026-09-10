"""Opt-in native export gate; weights are preplaced, never downloaded by tests.

SIMILARITY_CLIP_ASSETS points at the three pinned model files and manifest.json.
The normal PR lane uses small original ONNX contracts; this lane proves the real
CLIP towers and semantic endpoint work without an AI Search platform.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from printstash_core.inference import EmbeddingInput
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import PassageVector
from app.db.session import get_session_factory
from app.modules.inference import store
from app.modules.inference.local import configured_provider
from app.modules.inference.onnx_cpu import OnnxCpuProvider
from app.modules.inference.search import SearchRequest, search
from app.modules.similarity import configuration, runs


@pytest.fixture
def preplaced_clip(monkeypatch):
    value = os.environ.get("SIMILARITY_CLIP_ASSETS")
    if not value:
        raise RuntimeError("native CLIP lane requires preplaced SIMILARITY_CLIP_ASSETS")
    root = Path(value)
    assert root.is_dir(), "configured preplaced CLIP directory must exist"
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(root))
    monkeypatch.setitem(_overlay, "embedding_model_key", "clip-vit-base-patch32-fp32")
    return configured_provider(get_session_factory())


@pytest.fixture
def native_canary_towers(preplaced_clip, monkeypatch):
    # The golden-vector test performs this check itself so a native-architecture
    # failure reports its modality and numerical drift. The subprocess tests
    # below still exercise the production canary guard without any patch.
    monkeypatch.setattr(OnnxCpuProvider, "_check_canaries", lambda self: None)
    return OnnxCpuProvider(preplaced_clip.directory, preplaced_clip.manifest, 1)


class TestPreplacedClip:
    @pytest.mark.parametrize("modality", ["image", "text"])
    def test_preserves_pretrained_canary(self, native_canary_towers, modality):
        provider = native_canary_towers
        inputs = {
            "image": EmbeddingInput("image", rgb=bytes([127] * 3), width=1, height=1),
            "text": EmbeddingInput("text", text=provider.manifest.text.canary_text),
        }
        expected = {
            "image": provider.manifest.image.canary,
            "text": provider.manifest.text.canary,
        }

        actual = provider.embed((inputs[modality],), provider.space)[0]

        difference = float(np.max(np.abs(np.asarray(actual) - expected[modality])))
        assert difference <= provider.manifest.canary_tolerance, (
            f"{modality} canary: maximum absolute drift {difference:.8f}; "
            f"tolerance {provider.manifest.canary_tolerance}"
        )

    def test_runs_compatible_native_towers(self, preplaced_clip):
        provider = preplaced_clip
        provider.validate()
        vectors = np.array(
            provider.embed(
                (
                    EmbeddingInput("text", text="a bright red square"),
                    EmbeddingInput("image", rgb=bytes([255, 0, 0]), width=1, height=1),
                    EmbeddingInput("image", rgb=bytes([0, 0, 255]), width=1, height=1),
                ),
                provider.space,
            )
        )
        assert vectors.shape == (3, 512)
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-6)
        assert vectors[0] @ vectors[1] > vectors[0] @ vectors[2]

    def test_queries_text_through_standalone_index(
        self, preplaced_clip, db_session, make_file, make_model, make_user
    ):
        provider = preplaced_clip
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "embeddings_enabled": True}
        )
        generation = store.initialize(get_session_factory(), provider)
        file = make_file(make_model(), filename="red.stl")
        run = runs.start(db_session, actor)
        run, token = runs.claim(db_session)
        run.state = "running"
        db_session.add(run)
        db_session.commit()
        vector = provider.embed(
            (EmbeddingInput("image", rgb=bytes([255, 0, 0]), width=1, height=1),),
            provider.space,
        )[0]
        assert store.publish(
            db_session,
            actor,
            generation_id=generation,
            space=provider.space,
            file_id=file.id,
            component_index=0,
            input_hash=file.sha256,
            vector=vector,
            run_id=run.id,
            lease_token=token,
        )
        response = search(
            db_session, get_session_factory(), actor, SearchRequest(text="a red object")
        )
        assert response["index_state"] == "ready"
        assert response["items"][0]["model"]["id"] == file.model_id
        assert response["items"][0]["evidence_kind"] == "semantic"
        assert "exact_equivalence" not in response["items"][0]
        row = db_session.exec(select(PassageVector)).one()
        assert len(row.vector_blob) == 512 * 4
