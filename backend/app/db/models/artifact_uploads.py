"""Durable ownership and progress for resumable Artifact uploads."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel
from .types import ArtifactUploadState


class ArtifactUploadSession(SQLModel, table=True):
    """One owner-bound upload envelope, independent of its transfer adapter."""

    __tablename__ = "artifact_upload_sessions"
    __table_args__ = (
        CheckConstraint("declared_size >= 0", name="ck_artifact_upload_declared_size"),
        CheckConstraint(
            "received_bytes >= 0", name="ck_artifact_upload_received_bytes"
        ),
        CheckConstraint(
            "verified_size IS NULL OR verified_size >= 0",
            name="ck_artifact_upload_verified_size",
        ),
        CheckConstraint("version >= 0", name="ck_artifact_upload_version"),
        CheckConstraint(
            "state IN ('created', 'uploading', 'verifying', 'ingesting', 'completed', 'failed', 'aborted', 'expired')",
            name="ck_artifact_upload_state",
        ),
        Index(
            "ix_artifact_upload_owner_state_updated",
            "owner_user_id",
            "state",
            "updated_at",
        ),
    )

    id: str = Field(primary_key=True, max_length=64)
    owner_user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    purpose: str = Field(max_length=32, index=True)
    target_role: str = Field(max_length=32, index=True)
    target_id: Optional[str] = Field(default=None, max_length=128, index=True)
    filename: str = Field(max_length=512)
    media_type: str = Field(max_length=128)
    declared_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    client_sha256: Optional[str] = Field(default=None, max_length=64)
    state: ArtifactUploadState = Field(
        default=ArtifactUploadState.CREATED,
        sa_column=Column(String(16), nullable=False, index=True),
    )
    adapter_id: str = Field(max_length=32)
    destination_ref: Optional[str] = Field(default=None, max_length=512)
    protected_native_id: Optional[str] = Field(default=None, sa_column=Column(Text))
    staging_identity_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    received_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    error_code: Optional[str] = Field(default=None, max_length=128)
    retryable: bool = Field(default=False, index=True)
    verified_size: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    verified_sha256: Optional[str] = Field(default=None, max_length=64, index=True)
    background_job_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("background_jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    version: int = Field(default=0)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class ArtifactUploadPart(SQLModel, table=True):
    """A durable chunk or native-part receipt belonging to exactly one session."""

    __tablename__ = "artifact_upload_parts"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "part_number", name="uq_artifact_upload_part_number"
        ),
        CheckConstraint("part_number >= 1", name="ck_artifact_upload_part_number"),
        CheckConstraint("byte_offset >= 0", name="ck_artifact_upload_part_offset"),
        CheckConstraint("size_bytes > 0", name="ck_artifact_upload_part_size"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("artifact_upload_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    part_number: int
    byte_offset: int = Field(sa_column=Column(BigInteger, nullable=False))
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(max_length=64)
    provider_receipt_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, index=True)
