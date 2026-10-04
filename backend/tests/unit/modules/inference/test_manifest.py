"""Rendering changes invalidate derived spaces without rewriting encoder assets."""

import hashlib
import json
from dataclasses import replace

import pytest

from app.modules.inference.manifest import ImageTower, LocalModelManifest, ModelAsset


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
