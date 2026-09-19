"""Replay only measured native vectors; unknown inputs fail instead of inventing embeddings."""

import base64
import hashlib
import json
import struct
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingSpace


class PrecomputedEmbeddings:
    def __init__(self, path: Path):
        self.payload = json.loads(path.read_text())
        self.space = EmbeddingSpace(**self.payload["space"])

    def blob(self, text: str) -> bytes:
        key = hashlib.sha256(text.encode()).hexdigest()
        try:
            return base64.b64decode(self.payload["vectors"][key], validate=True)
        except KeyError:
            raise EmbeddingError("embedding_fixture_input_unavailable") from None

    def embed(self, inputs, space, *, context=None):
        if context is not None:
            context.remaining()
        if space != self.space:
            raise EmbeddingError("embedding_space_mismatch")
        return tuple(
            struct.unpack(f"<{space.dimension}f", self.blob(item.text))
            for item in inputs
        )
