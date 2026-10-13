"""Rendering changes invalidate derived spaces without rewriting encoder assets."""

import hashlib
import json
from dataclasses import replace

import pytest

from app.modules.inference.manifest import (
    ImageTower,
    LocalModelManifest,
    ModelAsset,
    PointModelManifest,
    validate_space,
)
from tests.paths import FIXTURES_DIR


@pytest.fixture
def manifest():
    return LocalModelManifest(
        model_key="render-contract",
        model_revision="r1",
        family="dino",
        native_dimension=3,
        image=ImageTower(
            graph=ModelAsset(filename="image.onnx", sha256="a" * 64), canary=(1, 0, 0)
        ),
    )


class TestLocalModelManifest:
    def test_invalidates_vectors_from_the_previous_portable_renderer(self, manifest):
        current = manifest.space()
        previous_recipe = json.loads(current.render_recipe)
        previous_recipe["rasterizer"] = "qualified-portable-v4"
        previous = replace(
            current, render_recipe=json.dumps(previous_recipe, sort_keys=True)
        )

        assert current.config_hash != previous.config_hash

    def test_invalidates_vectors_from_the_unfiltered_vertex_recipe(self, manifest):
        current = manifest.space()
        previous_recipe = json.loads(current.render_recipe)
        previous_recipe["rasterizer"] = "relative-f64-v1"
        previous = replace(
            current, render_recipe=json.dumps(previous_recipe, sort_keys=True)
        )

        assert current.config_hash != previous.config_hash

    def test_invalidates_vectors_from_the_world_float32_recipe(self, manifest):
        previous_recipe = json.dumps(
            {
                "version": "six-orthographic-matte-v1",
                "manifest_sha256": hashlib.sha256(
                    manifest.model_dump_json().encode()
                ).hexdigest(),
                "image_size": 224,
            },
            sort_keys=True,
        )

        current = manifest.space()

        previous = replace(current, render_recipe=previous_recipe)
        assert current.config_hash != previous.config_hash

    def test_preserves_encoder_manifest_provenance(self, manifest):
        serialized = manifest.model_dump_json()
        digest = hashlib.sha256(serialized.encode()).hexdigest()

        space = manifest.space()

        assert json.loads(space.render_recipe)["manifest_sha256"] == digest
        assert manifest.model_dump_json() == serialized
        assert manifest.assets() == (
            ModelAsset(filename="image.onnx", sha256="a" * 64),
        )


@pytest.fixture
def pinned_clip():
    return LocalModelManifest.model_validate_json(
        (FIXTURES_DIR / "embeddings/clip-vit-base-patch32-fp32.json").read_bytes()
    )


class TestEncoderAlignment:
    def test_accepts_existing_point_alignment(self, pinned_clip):
        historical = "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"
        point = PointModelManifest(
            model_key="pinned-point",
            model_revision="2" * 40,
            repository="printstash/contract",
            checkpoint_sha256="3" * 64,
            license="CC0-1.0",
            paired=pinned_clip,
            paired_space_hash=historical,
            point={
                "graph": {"filename": "point.onnx", "sha256": "4" * 64},
                "canary": [1.0] + [0.0] * 511,
            },
        )
        assert point.space().alignment_identity == historical
        assert point.paired.model_dump_json() == pinned_clip.model_dump_json()

    def test_renderer_changes_only_derived_identity(self, pinned_clip, monkeypatch):
        from app.modules.inference import manifest as owner

        historical = "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"
        source = pinned_clip.model_dump_json()
        before = pinned_clip.space()
        monkeypatch.setattr(owner, "RASTERIZER_RECIPE", "test-future-renderer")

        assert pinned_clip.encoder_space().config_hash == historical
        assert pinned_clip.space().config_hash != before.config_hash
        assert pinned_clip.model_dump_json() == source

    @pytest.mark.parametrize("profile", ["thumbnail", "multiview"])
    def test_visual_spaces_keep_native_alignment(self, pinned_clip, profile):
        from printstash_core.search.visual_inputs import VisualRecipe

        historical = "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"
        visual = VisualRecipe.space(
            pinned_clip.encoder_space(), image_size=224, profile=profile
        )

        validate_space(pinned_clip, visual)
        assert visual.alignment_identity == historical
        assert visual.config_hash != historical
        assert visual.config_hash != pinned_clip.space().config_hash
