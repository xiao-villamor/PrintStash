from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


JobState = Literal["pending", "running", "completed", "failed"]


class IngestResponse(BaseModel):
    """Returned immediately from POST /ingest/orca."""

    job_id: str
    state: JobState
    message: str = "ingestion queued"


class UrlIngestRequest(BaseModel):
    """Body for POST /ingest/url."""

    url: str
    collection: Optional[str] = None
    model_name: Optional[str] = None
    tags: Optional[str] = None


class ArchiveEntryRead(BaseModel):
    name: str
    size_bytes: int
    file_type: Optional[str] = None  # FileType value if importable, else None
    is_image: bool = False


class ArchiveManifest(BaseModel):
    """Returned after staging an archive; entries are selectable for import."""

    archive_id: str
    archive_name: str
    entries: list[ArchiveEntryRead]


class ArchiveSelectRequest(BaseModel):
    """Body for POST /ingest/archive/{archive_id}/select."""

    names: list[str]
    collection: Optional[str] = None
    tags: Optional[str] = None


class IngestJobStatus(BaseModel):
    job_id: str
    owner_user_id: Optional[int] = Field(default=None, exclude=True)
    state: JobState
    model_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Progress hints — additive, absent for clients that only know the
    # original state machine.
    step: Optional[int] = None
    total_steps: Optional[int] = None
    label: Optional[str] = None
    progress: Optional[float] = None  # 0–100
    result: Optional[dict[str, Any]] = None
