"""OpenAI-compatible text embeddings with native-dimension canary validation."""

from __future__ import annotations

import struct

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.vectors import normalize

from app.modules.inference.endpoint import EndpointConfig
from app.modules.inference.transport import post_json


class RemoteEmbeddingProvider:
    def __init__(self, endpoint: EndpointConfig, space: EmbeddingSpace):
        if (
            space.provider != "openai_compatible"
            or space.provider_config_hash != endpoint.identity
            or space.model_key != endpoint.model
            or space.model_revision != endpoint.revision
            or space.modality != "text"
        ):
            raise EmbeddingError("embedding_space_mismatch")
        self.endpoint = endpoint.model_copy(deep=True)
        self.space = space

    def validate(self) -> None:
        self.embed(
            (EmbeddingInput("text", text="PrintStash embedding capability probe"),),
            self.space,
        )

    def embed(
        self,
        inputs: tuple[EmbeddingInput, ...],
        space: EmbeddingSpace,
        *,
        context: InferenceContext | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        if space != self.space:
            raise EmbeddingError("embedding_space_mismatch")
        if not 1 <= len(inputs) <= 8:
            raise EmbeddingError("embedding_batch_budget")
        if any(item.modality != "text" for item in inputs):
            raise EmbeddingError("embedding_image_unavailable")
        if any(
            len(item.text or "") > self.endpoint.max_input_characters for item in inputs
        ):
            raise EmbeddingError("embedding_input_limit_exceeded")
        # Always request native dimensions. MRL/quantization belong to derived
        # generation indexes; do not ask a provider to discard the source float.
        body = post_json(
            self.endpoint,
            "embeddings",
            {
                "model": space.model_key,
                "input": [item.text for item in inputs],
                "encoding_format": "float",
            },
            context=context,
        )
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(inputs):
            raise EmbeddingError("embedding_response_invalid")
        vectors: dict[int, tuple[float, ...]] = {}
        for entry in data:
            if not isinstance(entry, dict):
                raise EmbeddingError("embedding_response_invalid")
            index, vector = entry.get("index"), entry.get("embedding")
            if (
                type(index) is not int
                or not 0 <= index < len(inputs)
                or index in vectors
                or not isinstance(vector, list)
            ):
                raise EmbeddingError("embedding_response_invalid")
            if len(vector) != space.dimension or any(
                type(value) not in (int, float) for value in vector
            ):
                raise EmbeddingError("embedding_dimension_mismatch")
            blob = normalize(vector, space.dimension)
            vectors[index] = struct.unpack(f"<{space.dimension}f", blob)
        return tuple(vectors[index] for index in range(len(inputs)))
