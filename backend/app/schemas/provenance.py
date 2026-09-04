"""App-level import location for the framework-neutral capture contract.

The durable contract is deliberately defined once in ``printstash_core`` so
resolvers, Inbox, and persistence cannot drift into incompatible manifests.
"""

from datetime import datetime
from typing import Literal

from printstash_core.imports import (
    CapturedField,
    CaptureFile,
    CaptureManifestV2,
    CaptureSource,
)
from printstash_core.imports.contracts import MAX_FIELD_VALUE_LENGTHS
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROVENANCE_FIELD_NAMES = frozenset(MAX_FIELD_VALUE_LENGTHS)


class ProvenanceFieldRead(BaseModel):
    field_name: str
    captured_value: str
    captured_origin: Literal["confirmed", "inferred"]
    user_value: str | None = None
    user_override_set: bool
    effective_value: str
    effective_origin: Literal["confirmed", "inferred", "user"]
    captured_at: datetime | None = None
    user_updated_at: datetime | None = None


class ProvenanceCaptureSummaryRead(BaseModel):
    id: int
    snapshot_sha256: str
    adapter_version: str
    source_revision: str | None = None
    captured_at: datetime
    checked_at: datetime


class ProvenanceSourceRead(BaseModel):
    id: int
    provider: str
    source_item_id: str | None = None
    canonical_url: str
    source_revision: str | None = None
    tags: list[str] = Field(default_factory=list)
    first_captured_at: datetime
    last_checked_at: datetime
    fields: list[ProvenanceFieldRead] = Field(default_factory=list)
    captures: list[ProvenanceCaptureSummaryRead] = Field(default_factory=list)


class ModelSourceCoverRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provenance_source_id: int
    content_type: Literal["image/webp"]
    size_bytes: int
    updated_at: datetime


class ModelProvenanceRead(BaseModel):
    schema_version: Literal[2] = 2
    sources: list[ProvenanceSourceRead] = Field(default_factory=list)


class ModelProvenancePatch(BaseModel):
    """Explicit user overrides. Empty string is a real override, not a clear."""

    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, str] = Field(
        default_factory=dict, max_length=len(PROVENANCE_FIELD_NAMES)
    )
    clear_overrides: list[str] = Field(
        default_factory=list, max_length=len(PROVENANCE_FIELD_NAMES)
    )

    @field_validator("overrides")
    @classmethod
    def _bounded_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - PROVENANCE_FIELD_NAMES:
            raise ValueError("unsupported_provenance_field")
        for name, field_value in value.items():
            if len(field_value) > MAX_FIELD_VALUE_LENGTHS[name]:
                raise ValueError("provenance_override_too_large")
            if any(ord(character) < 32 for character in field_value):
                raise ValueError("invalid_provenance_override")
        return value

    @field_validator("clear_overrides")
    @classmethod
    def _allowlisted_clears(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or set(value) - PROVENANCE_FIELD_NAMES:
            raise ValueError("unsupported_provenance_field")
        return value


__all__ = [
    "CaptureFile",
    "CaptureManifestV2",
    "CaptureSource",
    "CapturedField",
    "ModelProvenancePatch",
    "ModelProvenanceRead",
    "ProvenanceCaptureSummaryRead",
    "ProvenanceFieldRead",
    "ProvenanceSourceRead",
    "ModelSourceCoverRead",
]
