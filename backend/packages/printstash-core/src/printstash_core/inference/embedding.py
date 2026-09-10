"""Shareable contracts for local inference, independent of HTTP, ORM or ONNX."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal, Protocol


class EmbeddingError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EmbeddingSpace:
    model_key: str
    model_revision: str
    dimension: int
    modality: Literal["image", "text_image"]
    render_recipe: str
    provider: str = "onnx_cpu"
    profile: str = "mesh_view"
    normalization: Literal["l2"] = "l2"
    query_prefix: str = ""
    document_prefix: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.dimension) is not int
            or not 1 <= self.dimension <= 4096
            or self.modality not in ("image", "text_image")
            or self.normalization != "l2"
            or any(
                not value or len(value) > 128
                for value in (
                    self.model_key,
                    self.model_revision,
                    self.provider,
                    self.profile,
                )
            )
            or len(self.query_prefix) > 256
            or len(self.document_prefix) > 256
            or not self.render_recipe
            or len(self.render_recipe) > 16384
        ):
            raise EmbeddingError("embedding_space_invalid")

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class EmbeddingInput:
    """Raw RGB bytes or a bounded text string; paths are not provider inputs."""

    modality: Literal["image", "text"]
    text: str | None = None
    rgb: bytes | None = None
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        if self.modality == "text":
            valid = (
                self.text is not None
                and bool(self.text.strip())
                and len(self.text) <= 4096
                and self.rgb is None
                and self.width == self.height == 0
            )
        elif self.modality == "image":
            valid = (
                self.text is None
                and self.rgb is not None
                and type(self.width) is int
                and type(self.height) is int
                and 1 <= self.width <= 1024
                and 1 <= self.height <= 1024
                and len(self.rgb) == self.width * self.height * 3
            )
        else:
            valid = False
        if not valid:
            raise EmbeddingError("embedding_input_invalid")


class EmbeddingProvider(Protocol):
    """Return one native-dimensional L2 vector per input in the requested Space.

    Providers reject unsupported modalities, incompatible Space, nonfinite/zero
    output or a batch over eight items. A provider may use fewer items per call.
    """

    def embed(
        self, inputs: tuple[EmbeddingInput, ...], space: EmbeddingSpace
    ) -> tuple[tuple[float, ...], ...]: ...
