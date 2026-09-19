"""Thumbnail generation state and render-slot reservations."""

from datetime import datetime
from typing import ClassVar, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import ThumbnailGenerationState


class ThumbnailGeneration(SQLModel, table=True):
    """Durable identity and lease for one thumbnail source/recipe."""

    __tablename__ = "thumbnail_generations"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "source_sha256",
            "recipe_fingerprint",
            name="uq_thumbnail_generation_recipe",
        ),
        Index("ix_thumbnail_generation_state_lease", "state", "lease_expires_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    source_sha256: str = Field(max_length=64)
    recipe_fingerprint: str = Field(max_length=64)
    state: ThumbnailGenerationState = Field(
        default=ThumbnailGenerationState.PENDING,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    storage_key: Optional[str] = Field(default=None, max_length=2048)
    output_sha256: Optional[str] = Field(default=None, max_length=64)
    output_size_bytes: Optional[int] = Field(default=None, sa_type=BigInteger)
    output_etag: Optional[str] = Field(default=None, max_length=256)
    width: Optional[int] = None
    height: Optional[int] = None
    strategy: Optional[str] = Field(default=None, max_length=32)
    complete: bool = Field(default=False)
    failure_reason: Optional[str] = Field(default=None, max_length=64)
    attempts: int = Field(default=0)
    lease_token: Optional[str] = Field(default=None, max_length=64, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    duration_ms: Optional[int] = None
    peak_rss_bytes: Optional[int] = Field(default=None, sa_type=BigInteger)
    processing_policy: str = Field(
        default="on_demand",
        sa_column=Column(String(16), nullable=False, server_default="on_demand"),
    )
    selection_version: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ArtifactAnalysisGeneration(SQLModel, table=True):
    """Versioned metadata work, independent of thumbnail availability."""

    __tablename__ = "artifact_analysis_generations"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "file_id", "source_sha256", "recipe", name="uq_artifact_analysis_recipe"
        ),
        Index("ix_artifact_analysis_due", "state", "next_attempt_at"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", ondelete="CASCADE", index=True)
    source_sha256: str = Field(max_length=64)
    recipe: str = Field(default="metadata-v1", max_length=64)
    state: str = Field(default="pending", max_length=16)
    preserve_metadata: bool = False
    actor_user_id: Optional[int] = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    attempts: int = 0
    lease_token: Optional[str] = Field(default=None, max_length=64)
    lease_expires_at: Optional[datetime] = None
    next_attempt_at: datetime = Field(default_factory=utcnow)
    error_code: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class ThumbnailRenderSlot(SQLModel, table=True):
    """Database-backed render permit shared by every application instance."""

    __tablename__ = "thumbnail_render_slots"

    id: Optional[int] = Field(default=None, primary_key=True)
    slot_number: int = Field(unique=True, index=True)
    generation_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("thumbnail_generations.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    lease_token: Optional[str] = Field(default=None, max_length=64, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
