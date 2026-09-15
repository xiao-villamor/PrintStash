"""Untrusted chat output cannot escape closed schemas or trigger additional dialect work."""

from unittest.mock import patch

import pytest
from printstash_core.inference import EmbeddingError
from printstash_core.inference.chat import ChatInput

from app.modules.inference.chat import RemoteChatProvider
from app.modules.inference.endpoint import EndpointConfig

SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": False,
}
REQUEST = ChatInput("Describe", "boat", SCHEMA)


@pytest.fixture
def provider():
    return RemoteChatProvider(EndpointConfig(base_url="http://test/v1", model="test"))


class TestRemoteChatProvider:
    def test_rejects_numeric_overflow_in_structured_output(self, provider):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "number"}},
            "required": ["score"],
            "additionalProperties": False,
        }
        body = {
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"score":1e999}'}}
            ]
        }

        with patch("app.modules.inference.chat.post_json", return_value=body):
            with pytest.raises(EmbeddingError, match="chat_output_invalid"):
                provider.complete(ChatInput("Score", "boat", schema))

    @pytest.mark.parametrize(
        "body",
        [
            {"choices": []},
            {"choices": ["invalid"]},
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": "no"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "x" * 16385}}
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"caption":"one","caption":"two"}'},
                    }
                ]
            },
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"caption":NaN}'}}
                ]
            },
        ],
        ids=[
            "empty",
            "shape",
            "unfinished",
            "refused",
            "null",
            "over-cap",
            "duplicate-property",
            "nonfinite",
        ],
    )
    def test_rejects_malformed_chat_envelopes(self, provider, body):
        with patch("app.modules.inference.chat.post_json", return_value=body):
            with pytest.raises(EmbeddingError, match="chat_output_invalid"):
                provider.complete(REQUEST)

    @pytest.mark.parametrize(
        "schema",
        [
            {
                "type": "object",
                "additionalProperties": False,
                "description": "x" * 33000,
            },
            {"type": "object"},
            {"type": "array"},
            {"type": "object", "additionalProperties": False, "required": "invalid"},
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"type": "string", "pattern": "(a+)+"}},
            },
        ],
        ids=["bytes", "open-properties", "root-type", "invalid-schema", "regex"],
    )
    def test_rejects_unbounded_schemas(self, provider, schema):
        with pytest.raises(EmbeddingError, match="chat_schema_invalid"):
            provider.complete(ChatInput("Describe", "boat", schema))

    @pytest.mark.parametrize(
        "calls",
        [[], [{"type": "function", "function": {"name": "other", "arguments": "{}"}}]],
        ids=["empty", "wrong-function"],
    )
    def test_rejects_unrequested_tools(self, provider, calls):
        typed = RemoteChatProvider(provider.endpoint, dialect="tools")
        with patch(
            "app.modules.inference.chat.post_json",
            return_value={
                "choices": [
                    {"finish_reason": "tool_calls", "message": {"tool_calls": calls}}
                ]
            },
        ):
            with pytest.raises(EmbeddingError, match="chat_output_invalid"):
                typed.complete(REQUEST)

    def test_requires_image_capability(self, provider):
        with pytest.raises(EmbeddingError, match="chat_images_unavailable"):
            provider.complete(
                ChatInput("Describe", "boat", SCHEMA, (b"\xff\xd8\xffdata",))
            )

    def test_rejects_unknown_dialects(self, provider):
        with pytest.raises(EmbeddingError, match="chat_dialect_invalid"):
            RemoteChatProvider(provider.endpoint, dialect="unknown")

    @pytest.mark.parametrize(
        "body",
        [
            {"status": "incomplete"},
            {"status": "completed", "output": []},
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal"}],
                    }
                ],
            },
        ],
        ids=["unfinished", "missing-message", "refused"],
    )
    def test_rejects_malformed_responses_envelopes(self, provider, body):
        typed = RemoteChatProvider(provider.endpoint, dialect="responses")
        with patch("app.modules.inference.chat.post_json", return_value=body):
            with pytest.raises(EmbeddingError, match="chat_output_invalid"):
                typed.complete(REQUEST)
