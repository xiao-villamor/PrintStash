"""Encoder identity extends text recipes without changing existing Spaces."""

import pytest

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.search.text_inputs import TextRecipe


class TestTextRecipe:
    @pytest.mark.parametrize(
        "values",
        [
            {"passage_version": True},
            {"max_input_characters": 128.5},
            {"max_input_characters": "128"},
            {"encoder_manifest_sha256": 123},
        ],
    )
    def test_rejects_invalid_recipe_types(self, values):
        with pytest.raises(EmbeddingError, match="search_recipe_unavailable"):
            TextRecipe(**values)

    def test_preserves_recipe_identity_without_an_encoder(self):
        assert (
            TextRecipe().encode()
            == '{"max_input_characters":16384,"passage_version":1,"recipe":"semantic-text-v1"}'
        )

    def test_binds_local_recipes_to_the_encoder(self):
        recipe = TextRecipe(encoder_manifest_sha256="a" * 64)
        space = EmbeddingSpace(
            model_key="test",
            model_revision="v1",
            dimension=4,
            modality="text",
            render_recipe=recipe.encode(),
        )
        assert TextRecipe.for_space(space) == recipe
        assert '"encoder_manifest_sha256":"' + "a" * 64 + '"' in space.render_recipe

    @pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "g" * 64, "a" * 65])
    def test_rejects_malformed_encoder_digests(self, digest):
        with pytest.raises(EmbeddingError, match="search_recipe_unavailable"):
            TextRecipe(encoder_manifest_sha256=digest)

    @pytest.mark.parametrize("payload", ["not-json", "[]", '{"unknown":true}'])
    def test_rejects_malformed_recipe_json(self, payload):
        space = EmbeddingSpace("test", "v1", 4, "text", payload)
        with pytest.raises(EmbeddingError, match="search_recipe_unavailable"):
            TextRecipe.for_space(space)


class TestDocumentInput:
    @pytest.mark.parametrize("length,truncated", [(27, False), (28, False), (29, True)])
    def test_counts_prefix_characters_inside_the_budget(self, length, truncated):
        from printstash_core.search.text_inputs import document_input

        space = EmbeddingSpace(
            "test",
            "v1",
            4,
            "text",
            TextRecipe(max_input_characters=128).encode(),
            document_prefix="p" * 100,
        )
        value, actual = document_input(space, "x" * length)
        assert value.text == "p" * 100 + "x" * min(28, length)
        assert actual is truncated

    @pytest.mark.parametrize("prefix", ["p" * 128, "p" * 129])
    def test_rejects_prefixes_consuming_the_document_budget(self, prefix):
        from printstash_core.search.text_inputs import document_input

        space = EmbeddingSpace(
            "test",
            "v1",
            4,
            "text",
            TextRecipe(max_input_characters=128).encode(),
            document_prefix=prefix,
        )
        with pytest.raises(EmbeddingError, match="search_prefix_exceeds_budget"):
            document_input(space, "document")
