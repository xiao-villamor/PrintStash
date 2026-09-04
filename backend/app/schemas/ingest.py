from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

JobState = Literal["pending", "running", "completed", "failed"]
ImportStage = Literal[
    "resolving",
    "downloading",
    "inspecting",
    "extracting",
    "hashing",
    "ingesting",
    "thumbnailing",
    "completed",
]
ImportCompletion = Literal[
    "complete",
    "partial",
]
ThumbnailStatus = Literal[
    "generated",
    "fallback_generated",
    "skipped",
    "failed",
]


class ImportFailedItem(BaseModel):
    name: str
    reason: str
    retryable: bool = False


class IngestResponse(BaseModel):
    """Returned immediately from POST /ingest/orca."""

    job_id: str
    state: JobState
    message: str = "ingestion queued"


class UrlIngestRequest(BaseModel):
    """Body for POST /ingest/url.

    ``url`` may be a direct file/.zip link *or* a model *page* on a supported
    host (Printables / Thingiverse); pages are resolved to their download link
    server-side. Authenticated MakerWorld packages are captured by the browser
    extension and uploaded through the inbox. The optional cookie fields remain
    accepted for backwards compatibility; ``makerworld_cookie`` is ignored.
    """

    url: str
    collection: Optional[str] = None
    tags: Optional[str] = None
    makerworld_cookie: Optional[str] = None
    thingiverse_cookie: Optional[str] = None
    # When ``url`` is a *collection*, review the member list before importing
    # instead of auto-importing every member.
    review: bool = False


class ArchiveEntryRead(BaseModel):
    entry_id: str
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

    entry_ids: list[str] = []
    names: list[str] = []
    collection: Optional[str] = None
    tags: Optional[str] = None


class ModelFileRead(BaseModel):
    file_id: str
    name: str
    file_type: str  # stl / gcode / sla / other
    size: Optional[int] = None


class ModelFilesManifest(BaseModel):
    """Returned (as a job result) when a model page exposes multiple files."""

    files_token: str
    page_title: str
    files: list[ModelFileRead]


class FileSelectRequest(BaseModel):
    """Body for POST /ingest/url/files/{files_token}/select."""

    file_ids: list[str]
    collection: Optional[str] = None
    tags: Optional[str] = None


class CollectionMemberRead(BaseModel):
    source_id: str
    title: str
    page_url: str


class CollectionManifest(BaseModel):
    """Returned (as a job result) when a collection URL is imported in review mode."""

    collection_token: str
    collection_name: str
    target_collection: str
    members: list[CollectionMemberRead]


class CollectionSelectRequest(BaseModel):
    """Body for POST /ingest/collection/{collection_token}/select."""

    member_ids: list[str]
    collection: Optional[str] = None
    tags: Optional[str] = None


class IngestJobStatus(BaseModel):
    job_id: str
    owner_user_id: Optional[int] = Field(default=None, exclude=True)
    visible: bool = Field(default=True, exclude=True)
    state: JobState
    kind: str = "ingest"
    model_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    committed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Progress hints — additive, absent for clients that only know the
    # original state machine.
    step: Optional[int] = None
    total_steps: Optional[int] = None
    label: Optional[str] = None
    progress: Optional[float] = None  # 0–100
    result: Optional[dict[str, Any]] = None
    stage: Optional[ImportStage] = None
    current_item: Optional[str] = None
    processed: int = 0
    total: Optional[int] = None
    succeeded: int = 0
    deduplicated: int = 0
    skipped: int = 0
    failed: int = 0
    completion: Optional[ImportCompletion] = None
    thumbnail_status: Optional[ThumbnailStatus] = None
    thumbnail_reason: Optional[str] = None
    retryable: bool = False
    failed_items: list[ImportFailedItem] = Field(default_factory=list)
