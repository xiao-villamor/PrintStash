"""Structured chat DTOs bound text, images and generation budgets without framework imports."""

import pytest

from printstash_core.inference import EmbeddingError
from printstash_core.inference.chat import ChatInput, ChatResult


class TestChatInput:
    def test_accepts_bounded_multimodal_input(self):
        request = ChatInput(
            "Describe",
            "boat",
            {},
            image_jpegs=(b"\xff\xd8\xffdata",),
            max_output_tokens=64,
        )

        assert request.image_jpegs == (b"\xff\xd8\xffdata",)
        assert request.max_output_tokens == 64

    @pytest.mark.parametrize(
        "overrides",
        [
            {"instruction": ""},
            {"instruction": "x" * 8193},
            {"text": "x" * 16385},
            {"schema": []},
            {"image_jpegs": (b"\xff\xd8\xff",) * 13},
            {"image_jpegs": (b"\xff\xd8\xff" + b"x" * (512 * 1024),)},
            {"image_jpegs": (b"not-jpeg",)},
            {"max_output_tokens": True},
            {"max_output_tokens": 15},
            {"max_output_tokens": 2049},
        ],
        ids=[
            "empty-instruction",
            "instruction-cap",
            "text-cap",
            "schema-type",
            "image-count",
            "image-bytes",
            "image-format",
            "token-type",
            "token-min",
            "token-max",
        ],
    )
    def test_rejects_invalid_input(self, overrides):
        with pytest.raises(EmbeddingError, match="chat_input_invalid"):
            ChatInput(
                **(
                    {"instruction": "Describe", "text": "boat", "schema": {}}
                    | overrides
                )
            )


class TestChatResult:
    def test_reports_the_guarantee(self):
        result = ChatResult(
            {"caption": "boat"}, "json", "validated_json", repaired=True
        )

        assert (result.value, result.dialect, result.guarantee, result.repaired) == (
            {"caption": "boat"},
            "json",
            "validated_json",
            True,
        )
