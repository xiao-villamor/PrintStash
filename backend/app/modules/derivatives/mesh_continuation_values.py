"""Closed execution inputs for a pending mesh fingerprint continuation.

These values are input snapshots owned by derivatives, never output_json or
public metadata. Every field is present; explicit null records an absent source
attribute rather than allowing an arbitrary partial shape.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.db.models import FileType
from app.modules.derivatives.records import ArtifactSource


@dataclass(frozen=True)
class FingerprintPlan:
    algorithm_version: str
    triangle_cap: int

    def __post_init__(self) -> None:
        if (
            type(self.algorithm_version) is not str
            or not 1 <= len(self.algorithm_version) <= 128
        ):
            raise ValueError("invalid_continuation_algorithm")
        if (
            type(self.triangle_cap) is not int
            or not 100 <= self.triangle_cap <= MAX_ANALYSIS_FACES
        ):
            raise ValueError("invalid_continuation_face_cap")


def encode_source(source: ArtifactSource) -> str:
    raw = asdict(source)
    raw["file_type"] = source.file_type.value
    encoded = json.dumps(
        {"version": 1, "source": raw},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    # Validate the same strict contract at both input boundaries.
    decode_source(encoded)
    return encoded


def decode_source(encoded: str) -> ArtifactSource:
    raw = json.loads(encoded)
    if (
        type(raw) is not dict
        or set(raw) != {"version", "source"}
        or type(raw["version"]) is not int
        or raw["version"] != 1
    ):
        raise ValueError("invalid_continuation_source_version")
    source = raw["source"]
    if type(source) is not dict or set(source) != {
        field.name for field in fields(ArtifactSource)
    }:
        raise ValueError("invalid_continuation_source_shape")
    for name in ("sha256", "original_filename", "path"):
        if type(source[name]) is not str or not source[name]:
            raise ValueError("invalid_continuation_source_text")
    if len(source["sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in source["sha256"]
    ):
        raise ValueError("invalid_continuation_source_hash")
    for name in ("model_id", "size_bytes"):
        if type(source[name]) is not int or source[name] < (
            1 if name == "model_id" else 0
        ):
            raise ValueError("invalid_continuation_source_integer")
    if type(source["is_external"]) is not bool:
        raise ValueError("invalid_continuation_source_external")
    library_id = source["external_library_id"]
    if library_id is not None and (type(library_id) is not int or library_id <= 0):
        raise ValueError("invalid_continuation_source_library")
    for name in ("source_key", "source_etag", "source_version_id"):
        if source[name] is not None and type(source[name]) is not str:
            raise ValueError("invalid_continuation_source_attribute")
    mtime = source["source_mtime"]
    if mtime is not None and (
        type(mtime) not in (int, float) or not math.isfinite(mtime)
    ):
        raise ValueError("invalid_continuation_source_mtime")
    source["file_type"] = FileType(source["file_type"])
    return ArtifactSource(**source)
