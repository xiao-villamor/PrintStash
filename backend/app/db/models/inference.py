"""Shareable local embedding spaces and native float32 generations."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel


class InferenceEndpoint(SQLModel, table=True):
    """Immutable admin-validated endpoint version; credentials stay encrypted."""

    __audit_exclude__: ClassVar[bool] = True
    __tablename__ = "inference_endpoints"
    id: int | None = Field(default=None, primary_key=True)
    config_hash: str = Field(max_length=64, unique=True)
    kind: str = Field(max_length=16)
    config_json: str = Field(sa_column=Column(Text, nullable=False))
    api_key: str = Field(default="", sa_column=Column(EncryptedText(), nullable=False))
    headers_json: str = Field(
        default="{}", sa_column=Column(EncryptedText(), nullable=False)
    )
    native_dimension: int | None = Field(default=None)
    supports_images: bool = False
    dialect: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow)


class EmbeddingSpace(SQLModel, table=True):
    __tablename__ = "embedding_spaces"
    __table_args__ = (
        CheckConstraint(
            "native_dimension >= 1 AND native_dimension <= 4096", name="dimension_range"
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    config_hash: str = Field(max_length=64, unique=True)
    modality: str = Field(max_length=32)
    profile: str = Field(max_length=64)
    provider: str = Field(max_length=32)
    model_key: str = Field(max_length=128)
    model_revision: str = Field(max_length=128)
    native_dimension: int
    normalization: str = Field(default="l2", max_length=16)
    prefixes_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    recipe_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    config_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class IndexGeneration(SQLModel, table=True):
    __tablename__ = "index_generations"
    __table_args__ = (
        UniqueConstraint(
            "active_profile_key", name="uq_index_generation_active_profile"
        ),
        UniqueConstraint(
            "building_profile_key", name="uq_index_generation_building_profile"
        ),
        CheckConstraint(
            "index_dimension >= 1 AND index_dimension <= 4096", name="dimension_range"
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    space_id: int = Field(
        foreign_key="embedding_spaces.id", ondelete="CASCADE", index=True
    )
    active_profile_key: str | None = Field(default=None, max_length=128)
    building_profile_key: str | None = Field(default=None, max_length=128)
    state: str = Field(default="active", max_length=16)
    phase: str = Field(
        default="ready", max_length=32, sa_column_kwargs={"server_default": "ready"}
    )
    index_dimension: int
    quantization: str = Field(default="float32", max_length=16)
    index_backend: str = Field(default="numpy", max_length=32)
    transform_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False, server_default="{}")
    )
    vector_table_name: str | None = Field(default=None, max_length=64)
    index_state: str = Field(
        default="absent", max_length=16, sa_column_kwargs={"server_default": "absent"}
    )
    index_error: str | None = Field(default=None, max_length=64)
    indexed_after_id: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    version_token: str | None = Field(default=None, max_length=32)
    actor_id: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    replaces_generation_id: int | None = Field(default=None)
    lease_token: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = Field(default=None)
    cancel_requested: bool = Field(
        default=False, sa_column_kwargs={"server_default": "0"}
    )
    auto_activate: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    passage_after_id: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    reconcile_kind: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    processed: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    copied: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    truncated_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    verified_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    verified_at: datetime | None = Field(default=None)
    last_activity_at: datetime | None = Field(default=None)
    activated_at: datetime | None = Field(default=None)
    retired_at: datetime | None = Field(default=None)
    retain_until: datetime | None = Field(default=None)
    error_code: str | None = Field(default=None, max_length=64)
    job_id: str | None = Field(default=None, max_length=64)
    reservation_id: str | None = Field(default=None, max_length=128)
    estimated_bytes: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    created_at: datetime = Field(default_factory=utcnow)


class PassageVector(SQLModel, table=True):
    __audit_exclude__: ClassVar[bool] = True
    __tablename__ = "passage_vectors"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "unit_kind", "unit_key", name="uq_passage_vector_unit"
        ),
        CheckConstraint(
            "native_dimension >= 1 AND native_dimension <= 4096", name="dimension_range"
        ),
        Index("ix_passage_vector_scan", "generation_id", "id"),
        Index("ix_passage_vector_subject", "subject_type", "subject_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    generation_id: int = Field(foreign_key="index_generations.id", ondelete="CASCADE")
    unit_kind: str = Field(max_length=32)
    unit_key: str = Field(max_length=128)
    subject_type: str = Field(
        default="model", max_length=32, sa_column_kwargs={"server_default": "model"}
    )
    subject_id: int
    passage_id: int | None = Field(
        default=None, foreign_key="search_passages.id", ondelete="CASCADE", index=True
    )
    model_id: int | None = Field(
        default=None, foreign_key="models.id", ondelete="CASCADE", index=True
    )
    file_id: int | None = Field(
        default=None, foreign_key="files.id", ondelete="CASCADE", index=True
    )
    input_hash: str = Field(max_length=64)
    native_dimension: int
    truncated: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    vector_blob: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class SearchIndexFailure(SQLModel, table=True):
    """Hash-scoped retry/quarantine records never contain source text or replies."""

    __tablename__ = "search_index_failures"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "generation_id",
            "passage_id",
            "input_hash",
            name="uq_search_index_failure_unit",
        ),
        UniqueConstraint(
            "generation_id",
            "file_id",
            "input_hash",
            name="uq_search_index_failure_file",
        ),
        CheckConstraint(
            "(passage_id IS NOT NULL AND file_id IS NULL) OR (passage_id IS NULL AND file_id IS NOT NULL)",
            name="source_identity",
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    generation_id: int = Field(
        foreign_key="index_generations.id", ondelete="CASCADE", index=True
    )
    passage_id: int | None = Field(
        default=None, foreign_key="search_passages.id", ondelete="CASCADE", index=True
    )
    file_id: int | None = Field(
        default=None, foreign_key="files.id", ondelete="CASCADE", index=True
    )
    input_hash: str = Field(max_length=64)
    attempts: int = 0
    state: str = Field(default="retry", max_length=16)
    retry_after: datetime | None = Field(default=None)
    error_code: str = Field(max_length=64)


class SearchGenerationLease(SQLModel, table=True):
    """A short, durable reader pin survives worker/process boundaries."""

    __tablename__ = "search_generation_leases"
    __audit_exclude__: ClassVar[bool] = True
    token: str = Field(primary_key=True, max_length=64)
    generation_id: int = Field(
        foreign_key="index_generations.id", ondelete="CASCADE", index=True
    )
    expires_at: datetime = Field(index=True)
