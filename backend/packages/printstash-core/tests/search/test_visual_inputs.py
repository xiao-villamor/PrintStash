"""Visual identity and aggregation are independent of database/provider owners."""

from dataclasses import replace

import pytest

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.search.visual_inputs import VisualRecipe, mean_pool


def paired_space():
    return EmbeddingSpace("clip", "paired-r1", 3, "text_image", "legacy-render-v1")


class TestVisualRecipe:
    def test_binds_visual_space_identity(self):
        original = paired_space()
        thumbnail = VisualRecipe.space(original, image_size=224, profile="thumbnail")
        mean = VisualRecipe.space(original, image_size=224, profile="multiview")
        maximum = VisualRecipe.space(
            original, image_size=224, profile="multiview", aggregation="max"
        )
        assert (
            len(
                {
                    original.config_hash,
                    thumbnail.config_hash,
                    mean.config_hash,
                    maximum.config_hash,
                }
            )
            == 4
        )
        assert thumbnail.alignment_identity == original.config_hash
        assert (
            VisualRecipe.for_space(mean).view_identity
            == VisualRecipe.for_space(maximum).view_identity
        )
        assert VisualRecipe.for_space(mean).view_count == 6
        assert VisualRecipe.for_space(thumbnail).view_count == 1

    def test_rejects_text_only_or_unpaired_towers(self):
        with pytest.raises(EmbeddingError, match="embedding_alignment_unavailable"):
            VisualRecipe.space(
                replace(paired_space(), modality="text"),
                image_size=224,
                profile="thumbnail",
            )

    def test_rejects_a_mismatched_explicit_alignment(self):
        valid = VisualRecipe.space(paired_space(), image_size=224, profile="thumbnail")
        with pytest.raises(EmbeddingError, match="search_visual_recipe_invalid"):
            VisualRecipe.for_space(replace(valid, alignment_identity="f" * 64))

    def test_reuse_requires_identical_view_inputs(self):
        one = VisualRecipe.space(paired_space(), image_size=224, profile="multiview")
        other = VisualRecipe.space(
            replace(paired_space(), model_revision="paired-r2"),
            image_size=224,
            profile="multiview",
        )
        assert (
            VisualRecipe.for_space(one).view_identity
            != VisualRecipe.for_space(other).view_identity
        )


class TestMeanPool:
    def test_returns_a_normalized_mean_of_unit_views(self):
        assert mean_pool(((100, 0, 0), (0, 2, 0)), 3) == pytest.approx(
            (2**-0.5, 2**-0.5, 0)
        )

    @pytest.mark.parametrize(
        "vectors",
        [(), ((0, 0, 0),), ((float("nan"), 0, 1),), ((1, 0),), ((1, 0, 0), (-1, 0, 0))],
    )
    def test_rejects_invalid_views_instead_of_silently_omitting_them(self, vectors):
        with pytest.raises(EmbeddingError):
            mean_pool(vectors, 3)


class TestVisualRecipeAdmission:
    @pytest.mark.parametrize(
        "fields",
        [
            {"encoder_space_hash": "bad"},
            {"image_size": True},
            {"image_size": 513},
            {"profile": "point_cloud"},
            {"aggregation": "sum"},
            {"aggregation": "max"},
            {"version": "future"},
            {"thumbnail_recipe": "different"},
        ],
    )
    def test_rejects_incompatible_recipe_declarations(self, fields):
        with pytest.raises(EmbeddingError, match="search_visual_recipe_invalid"):
            VisualRecipe(
                **(
                    {
                        "encoder_space_hash": "a" * 64,
                        "image_size": 224,
                        "profile": "thumbnail",
                    }
                    | fields
                )
            )

    @pytest.mark.parametrize("payload", ["not-json", "[]", '{"unknown":true}'])
    def test_rejects_malformed_recipe_json(self, payload):
        space = VisualRecipe.space(paired_space(), image_size=224, profile="thumbnail")
        with pytest.raises(EmbeddingError, match="search_visual_recipe_invalid"):
            VisualRecipe.for_space(replace(space, render_recipe=payload))


class TestVisualInputCompatibility:
    def test_multiview_rejects_previous_renderer_recipe(self):
        import json

        current = VisualRecipe.space(
            paired_space(), image_size=224, profile="multiview"
        )
        old = json.loads(current.render_recipe)
        old["version"] = "canonical-views-media-thumbnail-v1"
        with pytest.raises(EmbeddingError, match="search_visual_recipe_invalid"):
            VisualRecipe.for_space(replace(current, render_recipe=json.dumps(old)))

    def test_thumbnail_recipe_retains_existing_version(self):
        recipe = VisualRecipe("a" * 64, 224, "thumbnail")
        assert recipe.version == "canonical-views-media-thumbnail-v1"
