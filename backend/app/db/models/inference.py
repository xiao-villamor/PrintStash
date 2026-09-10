"""Shareable local embedding spaces and native float32 generations."""

from datetime import datetime

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

from .base import SQLModel


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
        CheckConstraint(
            "index_dimension >= 1 AND index_dimension <= 4096", name="dimension_range"
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    space_id: int = Field(
        foreign_key="embedding_spaces.id", ondelete="CASCADE", index=True
    )
    active_profile_key: str | None = Field(default=None, max_length=128)
    state: str = Field(default="active", max_length=16)
    index_dimension: int
    quantization: str = Field(default="float32", max_length=16)
    index_backend: str = Field(default="numpy", max_length=32)
    created_at: datetime = Field(default_factory=utcnow)


class PassageVector(SQLModel, table=True):
    __tablename__ = "passage_vectors"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "unit_kind", "unit_key", name="uq_passage_vector_unit"
        ),
        CheckConstraint(
            "native_dimension >= 1 AND native_dimension <= 4096", name="dimension_range"
        ),
        Index("ix_passage_vector_scan", "generation_id", "id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    generation_id: int = Field(foreign_key="index_generations.id", ondelete="CASCADE")
    unit_kind: str = Field(max_length=32)
    unit_key: str = Field(max_length=128)
    model_id: int = Field(foreign_key="models.id", ondelete="CASCADE", index=True)
    file_id: int = Field(foreign_key="files.id", ondelete="CASCADE", index=True)
    input_hash: str = Field(max_length=64)
    native_dimension: int
    vector_blob: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
