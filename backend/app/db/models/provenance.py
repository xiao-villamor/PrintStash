"""Model sources, captured metadata and Artifact provenance links."""

from typing import Optional

from pydantic import NaiveDatetime
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel


class ModelProvenanceSource(SQLModel, table=True):
    """One captured remote source, owned by a single Model."""

    __tablename__ = "model_provenance_sources"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "identity_key", name="uq_provenance_source_identity"
        ),
        Index("ix_provenance_source_provider_item", "provider", "source_item_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("models.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(max_length=64, index=True)
    source_item_id: Optional[str] = Field(default=None, max_length=255)
    canonical_url: str = Field(max_length=2048)
    identity_key: str = Field(max_length=64, index=True)
    source_revision: Optional[str] = Field(default=None, max_length=255)
    tags_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    first_captured_at: NaiveDatetime = Field(default_factory=utcnow)
    last_checked_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    created_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    updated_at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class ModelSourceCover(SQLModel, table=True):
    """One private, normalized image in an ordered provenance gallery."""

    __tablename__ = "model_source_covers"
    __table_args__ = (
        UniqueConstraint(
            "provenance_source_id", "position", name="uq_source_image_position"
        ),
        CheckConstraint("position >= 0", name="ck_source_image_position"),
    )

    position: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provenance_source_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("model_provenance_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    storage_key: str = Field(max_length=2048, unique=True)
    content_type: str = Field(default="image/webp", max_length=64)
    size_bytes: int
    created_by: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class ModelProvenanceField(SQLModel, table=True):
    """Captured and explicitly-overridden value for one allowlisted field."""

    __tablename__ = "model_provenance_fields"
    __table_args__ = (
        UniqueConstraint(
            "provenance_source_id", "field_name", name="uq_provenance_field_name"
        ),
        CheckConstraint(
            "captured_origin IN ('confirmed', 'inferred')",
            name="ck_provenance_field_origin",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provenance_source_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("model_provenance_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    field_name: str = Field(max_length=64)
    captured_value_json: str = Field(sa_column=Column(Text, nullable=False))
    captured_origin: str = Field(max_length=16)
    user_value_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    user_override_set: bool = Field(default=False)
    user_updated_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    captured_at: Optional[NaiveDatetime] = Field(default=None)
    user_updated_at: Optional[NaiveDatetime] = Field(default=None)


class ProvenanceCapture(SQLModel, table=True):
    """Append-only normalized snapshot history for a provenance source."""

    __tablename__ = "provenance_captures"
    __table_args__ = (
        UniqueConstraint(
            "provenance_source_id",
            "snapshot_sha256",
            name="uq_provenance_capture_snapshot",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provenance_source_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("model_provenance_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    inbox_item_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("inbox_items.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    captured_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )
    adapter_version: str = Field(max_length=64)
    source_revision: Optional[str] = Field(default=None, max_length=255)
    snapshot_json: str = Field(sa_column=Column(Text, nullable=False))
    snapshot_sha256: str = Field(max_length=64, index=True)
    captured_at: NaiveDatetime = Field(default_factory=utcnow)
    checked_at: NaiveDatetime = Field(default_factory=utcnow)


class ArtifactProvenanceLink(SQLModel, table=True):
    """A source-file identity attached to one Artifact; it never owns bytes."""

    __tablename__ = "artifact_provenance_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provenance_source_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("model_provenance_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    capture_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("provenance_captures.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_file_id: Optional[str] = Field(default=None, max_length=255)
    source_filename: str = Field(max_length=512)
    container_entry_path: Optional[str] = Field(default=None, max_length=1024)
    source_revision: Optional[str] = Field(default=None, max_length=255)
    blob_sha256: str = Field(max_length=64, index=True)
    import_key: str = Field(max_length=64, unique=True, index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
