from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import CollectionRole, DocumentKind


class DocumentListItem(BaseModel):
    id: int
    name: str
    kind: DocumentKind
    collection: Optional[str] = None
    collection_id: Optional[int] = None
    multipart_model_id: Optional[int] = None
    filename: Optional[str] = None
    effective_role: Optional[CollectionRole] = None
    updated_at: datetime


class DocumentRead(DocumentListItem):
    body: Optional[str] = None  # markdown content (None for binary docs)


class DocumentCreate(BaseModel):
    """Create a markdown document. Binary docs come in via the upload endpoint."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    collection_id: Optional[int] = None
    multipart_model_id: Optional[int] = None
    body: Optional[str] = Field(default=None, max_length=1_000_000)


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    body: Optional[str] = Field(default=None, max_length=1_000_000)


class DocumentImageUpload(BaseModel):
    url: str
