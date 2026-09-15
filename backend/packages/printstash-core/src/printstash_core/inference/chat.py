"""Framework-free structured completion contract shared by optional consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from .context import InferenceContext
from .embedding import EmbeddingError


@dataclass(frozen=True)
class ChatInput:
    instruction: str
    text: str
    schema: dict[str, Any]
    image_jpegs: tuple[bytes, ...] = ()
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        if (
            not self.instruction
            or len(self.instruction) > 8192
            or len(self.text) > 16384
            or not isinstance(cast(object, self.schema), dict)
            or len(self.image_jpegs) > 12
            or sum(len(image) for image in self.image_jpegs) > 512 * 1024
            or any(not image.startswith(b"\xff\xd8\xff") for image in self.image_jpegs)
            or type(self.max_output_tokens) is not int
            or not 16 <= self.max_output_tokens <= 2048
        ):
            raise EmbeddingError("chat_input_invalid")


@dataclass(frozen=True)
class ChatResult:
    value: dict[str, Any]
    dialect: Literal["json_schema", "tools", "json", "responses"]
    guarantee: Literal["schema_constrained", "tool_constrained", "validated_json"]
    repaired: bool = False


class ChatProvider(Protocol):
    def complete(
        self, request: ChatInput, *, context: InferenceContext | None = None
    ) -> ChatResult: ...
