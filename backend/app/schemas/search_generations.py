"""Immutable generation proposals and administrative work status."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint_id: int | None = Field(default=None, ge=1)
    local_model_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    profile: Literal["semantic_text", "thumbnail", "multiview", "point_cloud"] = (
        "semantic_text"
    )
    aggregation: Literal["mean", "max"] = "mean"
    query_prefix: str | None = Field(default=None, max_length=256)
    document_prefix: str | None = Field(default=None, max_length=256)
    passage_recipe_version: int | None = Field(default=None, ge=1, le=2)
    index_backend: Literal["auto", "numpy", "sqlite_vec", "pgvector"] = "auto"
    index_dimension: int | None = Field(default=None, ge=1, le=4096)
    quantization: Literal["float32", "int8", "binary"] = "float32"
    auto_activate: bool = True

    @model_validator(mode="after")
    def require_one_provider(self):
        if (self.endpoint_id is None) == (self.local_model_id is None):
            raise ValueError("search_one_provider_required")
        if self.profile != "semantic_text" and (
            self.endpoint_id is not None
            or self.query_prefix is not None
            or self.document_prefix is not None
        ):
            raise ValueError("search_visual_requires_local_paired_encoder")
        if self.aggregation != "mean" and self.profile != "multiview":
            raise ValueError("search_aggregation_unavailable")
        return self


class GenerationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class GenerationRead(BaseModel):
    id: int
    state: str
    phase: str
    version_token: str | None
    modality: str
    profile: str
    model: str
    config_hash: str
    native_dimension: int
    index_dimension: int
    quantization: str
    index_backend: str
    effective_backend: str
    index_state: str
    index_error: str | None
    error_code: str | None
    processed: int
    copied: int
    truncated_count: int
    eligible: int
    indexed: int
    quarantined: int
    estimated_bytes: int
    job_id: str | None
    verified_at: datetime | None
    retain_until: datetime | None
    created_at: datetime
    last_activity_at: datetime | None
    eta_seconds: int | None


class GenerationEstimate(BaseModel):
    passages: int
    estimated_bytes: int
    existing_bytes: int
    budget_bytes: int
    fits_budget: bool
    estimated_seconds: int | None
