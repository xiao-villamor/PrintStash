"""Public, provider-neutral schemas for resumable Artifact uploads."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.db.models import FileRevisionStatus

UploadPurpose = Literal[
    "model",
    "gcode",
    "revision",
    "archive",
    "browser_capture",
    "url_import",
    "slicer",
    "external_writeback",
]


class ArtifactUploadCreate(BaseModel):
    purpose: UploadPurpose
    target_role: str = Field(min_length=1, max_length=32)
    target_id: str | None = Field(default=None, max_length=128)
    filename: str = Field(min_length=1, max_length=512)
    media_type: str = Field(default="application/octet-stream", max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    model_name: str | None = Field(default=None, max_length=255)
    collection: str | None = Field(default=None, max_length=1024)
    tags: str | None = Field(default=None, max_length=2048)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    target_library_id: int | None = Field(default=None, ge=1)
    revision_label: str | None = Field(default=None, max_length=128)
    revision_status: FileRevisionStatus | None = FileRevisionStatus.NEEDS_TEST
    revision_notes: str | None = Field(default=None, max_length=4096)
    is_recommended: bool = False

    @field_validator("filename")
    @classmethod
    def filename_is_a_leaf(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("filename_must_be_a_leaf")
        return value


class ArtifactUploadPartRead(BaseModel):
    index: int
    offset: int
    size_bytes: int
    sha256: str


class ArtifactUploadRead(BaseModel):
    id: str
    purpose: str
    target_role: str
    target_id: str | None
    filename: str
    media_type: str
    size_bytes: int
    state: str
    mode: str
    received_bytes: int
    verified_size: int | None
    verified_sha256: str | None
    job_id: str | None
    retryable: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    parts: list[ArtifactUploadPartRead] = Field(default_factory=list)


class ArtifactUploadPlanRead(BaseModel):
    session_id: str
    mode: Literal["api_chunks", "native_parts", "simple"]
    chunk_size: int
    max_parallel: int
    upload_path: str
    uploaded_parts: list[ArtifactUploadPartRead]
    expires_at: datetime


class ArtifactUploadChunkRead(BaseModel):
    session: ArtifactUploadRead
    part: ArtifactUploadPartRead
