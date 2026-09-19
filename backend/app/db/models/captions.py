"""Human-editable generated text is separate from every library description."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, Column, Index, Text, UniqueConstraint
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel


class SubjectCaption(SQLModel, table=True):
    __tablename__ = "subject_captions"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint("subject_type", "subject_id", name="uq_subject_caption"),
        CheckConstraint(
            "(subject_type='model' AND model_id IS NOT NULL AND model_id=subject_id AND collection_id IS NULL AND multipart_model_id IS NULL AND document_id IS NULL) OR "
            "(subject_type='collection' AND collection_id IS NOT NULL AND collection_id=subject_id AND model_id IS NULL AND multipart_model_id IS NULL AND document_id IS NULL) OR "
            "(subject_type='multipart_model' AND multipart_model_id IS NOT NULL AND multipart_model_id=subject_id AND model_id IS NULL AND collection_id IS NULL AND document_id IS NULL) OR "
            "(subject_type='document' AND document_id IS NOT NULL AND document_id=subject_id AND model_id IS NULL AND collection_id IS NULL AND multipart_model_id IS NULL)",
            name="subject_caption_owner",
        ),
        CheckConstraint("subject_id > 0", name="subject_caption_id"),
        CheckConstraint(
            "state IN ('generated','edited','dismissed')", name="subject_caption_state"
        ),
        CheckConstraint(
            "phase IN ('pending','running','ready','failed')",
            name="subject_caption_phase",
        ),
        CheckConstraint(
            "length(text) <= 2048 AND (state != 'dismissed' OR text = '')",
            name="subject_caption_text",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="subject_caption_attempts"
        ),
        Index("ix_subject_caption_work", "phase", "retry_after", "id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    subject_type: str = Field(max_length=32)
    subject_id: int
    # The four foreign keys prevent a purge and later ID reuse from attaching
    # edited text or a dismissal to a different Subject.
    model_id: int | None = Field(
        default=None, foreign_key="models.id", ondelete="CASCADE", index=True
    )
    collection_id: int | None = Field(
        default=None, foreign_key="collections.id", ondelete="CASCADE", index=True
    )
    multipart_model_id: int | None = Field(
        default=None, foreign_key="multipart_models.id", ondelete="CASCADE", index=True
    )
    document_id: int | None = Field(
        default=None, foreign_key="documents.id", ondelete="CASCADE", index=True
    )
    state: str = Field(default="generated", max_length=16)
    phase: str = Field(default="pending", max_length=16)
    text: str = Field(default="", sa_column=Column(Text, nullable=False))
    version_token: str = Field(max_length=32)
    input_hash: str = Field(default="", max_length=64)
    source_file_id: int | None = Field(
        default=None, foreign_key="files.id", ondelete="SET NULL"
    )
    recipe: str = Field(default="caption-thumbnail-v1", max_length=128)
    endpoint_id: int | None = Field(
        default=None, foreign_key="inference_endpoints.id", ondelete="SET NULL"
    )
    provider_identity: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=256)
    model_revision: str = Field(default="", max_length=128)
    actor_id: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    edited_by: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    attempts: int = 0
    error_code: str | None = Field(default=None, max_length=64)
    retry_after: datetime | None = None
    lease_token: str | None = Field(default=None, max_length=32)
    lease_expires_at: datetime | None = None
    job_id: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
