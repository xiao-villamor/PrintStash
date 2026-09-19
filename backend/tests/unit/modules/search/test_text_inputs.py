"""Immutable embedding recipes preserve provider prefixes inside declared input budgets."""

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingSpace

from app.modules.search.text_inputs import TextRecipe, document_input


@pytest.fixture
def text_space():
    return EmbeddingSpace(
        model_key="test",
        model_revision="v1",
        dimension=4,
        modality="text",
        profile="semantic_text",
        render_recipe=TextRecipe(max_input_characters=128).encode(),
        provider="openai_compatible",
        document_prefix="passage: ",
    )


class TestDocumentInput:
    def test_preserves_document_prefix_inside_budget(self, text_space):
        value, truncated = document_input(text_space, "a" * 128)

        assert value.text == "passage: " + "a" * 119
        assert truncated is True

    def test_preserves_short_inputs(self, text_space):
        value, truncated = document_input(text_space, "boat")

        assert value.text == "passage: boat"
        assert truncated is False


class TestTextRecipe:
    @pytest.mark.parametrize(
        "values",
        [
            {"passage_version": 999},
            {"recipe": "unknown"},
            {"max_input_characters": 127},
            {"max_input_characters": 16385},
        ],
        ids=["passage-version", "text-recipe", "small-budget", "large-budget"],
    )
    def test_rejects_unavailable_recipes(self, values):
        with pytest.raises(EmbeddingError, match="search_recipe_unavailable"):
            TextRecipe(**values)

    def test_recovers_the_immutable_recipe(self, text_space):
        assert TextRecipe.for_space(text_space) == TextRecipe(max_input_characters=128)
