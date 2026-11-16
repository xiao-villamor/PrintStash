"""Request and manifest shapes of the ingestion endpoints.

Job status lives in ``app.schemas.jobs``; every ingest endpoint returns a
``JobAccepted``.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.modules.ingestion.archive_plan import ArchiveIntake


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
    is_document: bool = False


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
    intake: ArchiveIntake | None = None


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
