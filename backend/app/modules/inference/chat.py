"""Schema-first chat completions with a bounded, locally validated fallback."""

from __future__ import annotations

import base64
import json
import math
import threading
from copy import deepcopy

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from printstash_core.inference import EmbeddingError
from printstash_core.inference.chat import ChatInput, ChatResult
from printstash_core.inference.context import InferenceContext

from app.modules.inference.endpoint import EndpointConfig
from app.modules.inference.transport import EndpointError, post_json


def _validator(schema: dict) -> Draft202012Validator:
    """Only bounded, closed inline schemas: never retrieve a model-supplied URL."""
    try:
        if len(json.dumps(schema, allow_nan=False)) > 32768:
            raise ValueError()
        pending = [(schema, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 12:
                raise ValueError()
            if isinstance(node, dict):
                if any(
                    key in node
                    for key in (
                        "$ref",
                        "$dynamicRef",
                        "$id",
                        "pattern",
                        "patternProperties",
                    )
                ):
                    raise ValueError()
                if (node.get("type") == "object" or "properties" in node) and node.get(
                    "additionalProperties"
                ) is not False:
                    raise ValueError()
                pending.extend(
                    (value, depth + 1)
                    for value in node.values()
                    if isinstance(value, (dict, list))
                )
            elif isinstance(node, list):
                pending.extend(
                    (value, depth + 1)
                    for value in node
                    if isinstance(value, (dict, list))
                )
        if schema.get("type") != "object":
            raise ValueError()
        Draft202012Validator.check_schema(schema)
    except (ValueError, TypeError, RecursionError, SchemaError):
        raise EmbeddingError("chat_schema_invalid") from None
    return Draft202012Validator(deepcopy(schema))


def _parse(body: dict, dialect: str, validator: Draft202012Validator) -> dict:
    def constant(_value):
        raise ValueError()

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    try:
        choices = body["choices"]
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or choices[0].get("finish_reason") not in {"stop", "tool_calls"}
        ):
            raise ValueError()
        message = choices[0]["message"]
        if message.get("refusal"):
            raise ValueError()
        content = message.get("content")
        if dialect == "tools":
            calls = message["tool_calls"]
            if (
                len(calls) != 1
                or calls[0].get("type") != "function"
                or calls[0]["function"]["name"] != "structured_result"
            ):
                raise ValueError()
            content = calls[0]["function"]["arguments"]
        if not isinstance(content, str) or len(content) > 16384:
            raise ValueError()
        value = json.loads(
            content,
            parse_constant=constant,
            parse_float=finite,
            object_pairs_hook=unique,
        )
        validator.validate(value)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        RecursionError,
        ValidationError,
    ):
        raise EmbeddingError("chat_output_invalid") from None
    return value


class RemoteChatProvider:
    def __init__(
        self,
        endpoint: EndpointConfig,
        *,
        supports_images: bool = False,
        dialect: str | None = None,
    ):
        if dialect not in {None, "json_schema", "tools", "json", "responses"}:
            raise EmbeddingError("chat_dialect_invalid")
        self.endpoint = endpoint.model_copy(deep=True)
        self.supports_images = supports_images
        self._dialect = dialect
        self._lock = threading.Lock()

    def complete(
        self, request: ChatInput, *, context: InferenceContext | None = None
    ) -> ChatResult:
        validator = _validator(request.schema)
        if request.image_jpegs and not self.supports_images:
            raise EmbeddingError("chat_images_unavailable")
        context = context or InferenceContext.bounded(self.endpoint.timeout_seconds)
        schema = deepcopy(request.schema)
        content: str | list = request.text
        if request.image_jpegs:
            content = [{"type": "text", "text": request.text}] + [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(data).decode(),
                        "detail": "low",
                    },
                }
                for data in request.image_jpegs
            ]
        messages = [
            {
                "role": "system",
                "content": request.instruction
                + "\nReturn only a JSON object satisfying this schema: "
                + json.dumps(schema, separators=(",", ":")),
            },
            {"role": "user", "content": content},
        ]
        with self._lock:
            start = self._dialect
        if start == "responses" or (start is None and self.endpoint.prefer_responses):
            try:
                result = self._responses(request, validator, context)
            except EndpointError as exc:
                if not (exc.unsupported or exc.status == 404):
                    raise
                start = None
            else:
                with self._lock:
                    self._dialect = "responses"
                return result
        dialects = ("json_schema", "tools", "json")
        for dialect in dialects[dialects.index(start) if start else 0 :]:
            payload = {
                "model": self.endpoint.model,
                "messages": messages,
                "max_tokens": request.max_output_tokens,
                "temperature": 0,
                "stream": False,
            }
            if dialect == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_result",
                        "strict": True,
                        "schema": schema,
                    },
                }
            elif dialect == "tools":
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": "structured_result",
                            "description": "Return the requested structured result",
                            "parameters": schema,
                        },
                    }
                ]
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": "structured_result"},
                }
            # Strict JSON fallback is prompt + local validation. Do not require
            # json_object response_format from endpoints that reject all formats.
            try:
                body = post_json(
                    self.endpoint, "chat/completions", payload, context=context
                )
            except EndpointError as exc:
                if exc.unsupported and dialect != "json":
                    continue
                raise
            repaired = False
            try:
                value = _parse(body, dialect, validator)
            except EmbeddingError:
                if dialect != "json":
                    raise
                # One repair, same deadline/token cap, no raw invalid completion
                # fed back as instructions or copied into diagnostics.
                payload["messages"] = messages + [
                    {
                        "role": "user",
                        "content": "The previous output was invalid. Return one JSON object conforming exactly to the schema, with no additional properties or surrounding text.",
                    }
                ]
                value = _parse(
                    post_json(
                        self.endpoint, "chat/completions", payload, context=context
                    ),
                    dialect,
                    validator,
                )
                repaired = True
            with self._lock:
                self._dialect = dialect
            guarantee = {
                "json_schema": "schema_constrained",
                "tools": "tool_constrained",
                "json": "validated_json",
            }[dialect]
            return ChatResult(value, dialect, guarantee, repaired)
        raise EmbeddingError("chat_dialect_unavailable")

    def _responses(
        self,
        request: ChatInput,
        validator: Draft202012Validator,
        context: InferenceContext,
    ) -> ChatResult:
        content = [{"type": "input_text", "text": request.text}] + [
            {
                "type": "input_image",
                "image_url": "data:image/jpeg;base64,"
                + base64.b64encode(image).decode(),
                "detail": "low",
            }
            for image in request.image_jpegs
        ]
        body = post_json(
            self.endpoint,
            "responses",
            {
                "model": self.endpoint.model,
                "instructions": request.instruction,
                "input": [{"role": "user", "content": content}],
                "max_output_tokens": request.max_output_tokens,
                "store": False,
                "stream": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "structured_result",
                        "schema": request.schema,
                        "strict": True,
                    }
                },
            },
            context=context,
        )
        try:
            if body.get("status") != "completed" or body.get("error"):
                raise ValueError()
            messages = [item for item in body["output"] if item["type"] == "message"]
            if len(messages) != 1 or messages[0].get("role") != "assistant":
                raise ValueError()
            parts = messages[0]["content"]
            if len(parts) != 1 or parts[0]["type"] != "output_text":
                raise ValueError()
            value = _parse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": parts[0]["text"]},
                        }
                    ]
                },
                "json_schema",
                validator,
            )
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise EmbeddingError("chat_output_invalid") from None
        return ChatResult(value, "responses", "schema_constrained")
