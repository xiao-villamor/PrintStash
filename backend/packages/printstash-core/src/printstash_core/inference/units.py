"""Stable Artifact/component identity, independent of consumer algorithm versions."""

import hashlib
import re

from .embedding import EmbeddingError


def unit_key(file_id: int, component_index: int, input_hash: str, recipe: str) -> str:
    if (
        not 1 <= file_id < 2**63
        or not 0 <= component_index <= 2048
        or re.fullmatch(r"[0-9a-f]{64}", input_hash) is None
    ):
        raise EmbeddingError("embedding_unit_invalid")
    # Full Space identity lives on the generation; the recipe suffix aids human
    # inspection and component identity remains parseable without a second index.
    return f"mesh:{file_id}:{component_index}:{input_hash}:{hashlib.sha256(recipe.encode()).hexdigest()[:16]}"


def unit_component(key: str) -> int | None:
    match = re.fullmatch(
        r"mesh:[1-9][0-9]{0,18}:([0-9]{1,4}):[0-9a-f]{64}:[0-9a-f]{16}", key
    )
    if match is None or int(match[1]) > 2048:
        return None
    return int(match[1])
