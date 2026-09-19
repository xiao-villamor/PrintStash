"""Point recipes bind the exact paired encoder independently of storage or ONNX."""

from dataclasses import replace

import pytest

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.search.point_inputs import PointRecipe


@pytest.fixture
def point_space():
    recipe = PointRecipe("a" * 64, "b" * 64)
    return EmbeddingSpace(
        "point",
        "v1",
        512,
        "point_cloud",
        recipe.encode(),
        profile="point_cloud",
        alignment_identity="b" * 64,
    )


class TestPointRecipe:
    def test_binds_the_exact_paired_encoder(self, point_space):
        assert PointRecipe.for_space(point_space) == PointRecipe("a" * 64, "b" * 64)
        assert PointRecipe.for_space(point_space).encode() == point_space.render_recipe

    @pytest.mark.parametrize(
        "fields",
        [
            {"encoder_space_hash": "wrong"},
            {"paired_space_hash": "c" * 65},
            {"version": "future"},
            {"profile": "thumbnail"},
        ],
    )
    def test_rejects_incompatible_recipe_declarations(self, fields):
        with pytest.raises(EmbeddingError, match="embedding_point_recipe_invalid"):
            PointRecipe(
                **(
                    {"encoder_space_hash": "a" * 64, "paired_space_hash": "b" * 64}
                    | fields
                )
            )

    @pytest.mark.parametrize(
        "fields",
        [
            {"profile": "multiview"},
            {"modality": "text_image"},
            {"alignment_identity": "c" * 64},
        ],
    )
    def test_rejects_mismatched_spaces(self, point_space, fields):
        with pytest.raises(EmbeddingError, match="embedding_point_recipe_invalid"):
            PointRecipe.for_space(replace(point_space, **fields))

    @pytest.mark.parametrize("payload", ["not-json", "[]", '{"unknown":true}'])
    def test_rejects_malformed_recipe_json(self, point_space, payload):
        with pytest.raises(EmbeddingError, match="embedding_point_recipe_invalid"):
            PointRecipe.for_space(replace(point_space, render_recipe=payload))
