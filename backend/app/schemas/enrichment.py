"""Readiness of optional results, independent from durable source availability."""

from typing import Literal

from pydantic import BaseModel

EnrichmentState = Literal[
    "unknown",
    "pending",
    "running",
    "ready",
    "failed",
    "blocked",
    "disabled",
    "on_demand",
    "not_applicable",
]


class ArtifactEnrichmentRead(BaseModel):
    metadata: EnrichmentState = "unknown"
    thumbnail: EnrichmentState = "unknown"
    metadata_error: str | None = None
    thumbnail_error: str | None = None


class ArtifactEnrichmentRequest(BaseModel):
    metadata: bool = True
    thumbnail: bool = True
