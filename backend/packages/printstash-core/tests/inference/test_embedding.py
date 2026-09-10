"""Provider values reject ambiguous input and identify the entire immutable recipe."""

from dataclasses import replace

import pytest

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace


@pytest.fixture
def space():
    return EmbeddingSpace(
        "clip", "sha256:123", 3, "text_image", '{"render":"six-view-v1"}'
    )


class TestEmbeddingSpace:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("dimension", 0),
            ("dimension", 4097),
            ("dimension", True),
            ("modality", "other"),
            ("normalization", "none"),
            ("model_key", ""),
            ("query_prefix", "x" * 257),
            ("document_prefix", "x" * 257),
            ("render_recipe", ""),
            ("render_recipe", "x" * 16385),
        ],
    )
    def test_rejects_invalid_contract(self, space, field, value):
        with pytest.raises(EmbeddingError, match="embedding_space_invalid"):
            replace(space, **{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("model_revision", "new"),
            ("render_recipe", "new"),
            ("query_prefix", "query: "),
            ("dimension", 4),
            ("modality", "image"),
        ],
    )
    def test_isolates_changed_space(self, space, field, value):
        assert replace(space, **{field: value}).config_hash != space.config_hash
        assert replace(space).config_hash == space.config_hash


class TestEmbeddingInput:
    def test_accepts_bounded_rgb(self):
        assert EmbeddingInput("image", rgb=b"abc", width=1, height=1).rgb == b"abc"

    def test_accepts_text(self):
        assert EmbeddingInput("text", text="a bracket").text == "a bracket"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"modality": "image", "rgb": b"abc", "width": True, "height": 1},
            {"modality": "image", "rgb": b"abc", "width": 1, "height": 1.0},
            {"modality": "other"},
            {"modality": "text", "text": " "},
            {"modality": "text", "text": "x" * 4097},
            {"modality": "text", "text": "x", "rgb": b"abc"},
            {"modality": "image", "rgb": b"x", "width": 1, "height": 1},
            {"modality": "image", "rgb": b"", "width": 0, "height": 1},
        ],
    )
    def test_rejects_invalid_input(self, kwargs):
        with pytest.raises(EmbeddingError, match="embedding_input_invalid"):
            EmbeddingInput(**kwargs)
