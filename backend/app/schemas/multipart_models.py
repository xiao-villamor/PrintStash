"""API contracts for standalone multipart model compositions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.db.models import CollectionRole
from app.schemas.documents import DocumentListItem


class MultipartModelListItem(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    collection: Optional[str] = None
    collection_id: Optional[int] = None
    part_count: int = 0
    model_count: int = 0
    guide_count: int = 0
    cover_model_id: Optional[int] = None
    cover_image_url: Optional[str] = None
    cover_image_uploaded: bool = False
    cover_thumbnail_url: Optional[str] = None
    member_model_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    starred: bool = False
    effective_role: Optional[CollectionRole] = None
    updated_at: datetime


class MultipartModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1_000_000)
    collection_id: Optional[int] = None


class MultipartModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1_000_000)
    cover_image_url: Optional[AnyHttpUrl] = None


class MultipartMemberRead(BaseModel):
    """A live readable member or a deliberately redacted unavailable member."""

    id: int
    choice_id: int | None = None
    legacy_label: str | None = None
    source_file_id: int | None = None
    name: Optional[str] = None
    slug: Optional[str] = None
    thumbnail_url: Optional[str] = None
    source_file_count: int = 0
    gcode_revision_count: int = 0
    available: bool = True


class MultipartPartRead(BaseModel):
    quantity: int = 1
    id: int
    name: str
    sort_order: int
    models: list[MultipartMemberRead] = Field(default_factory=list)


class MultipartModelRead(MultipartModelListItem):
    created_at: datetime
    parts: list[MultipartPartRead] = Field(default_factory=list)
    guides: list[DocumentListItem] = Field(default_factory=list)


class MultipartPartWrite(BaseModel):
    quantity: int = Field(default=1, ge=1, le=10_000)
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    choices: list["MultipartChoiceWrite"] | None = Field(
        default=None, min_length=1, max_length=100
    )
    # Kept as a read/write compatibility bridge for API clients from the first
    # 0.13 preview. New clients must send ``choices`` so an existing choice's
    # identity can be retained when it is not currently readable.
    model_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)


class MultipartChoiceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: int = Field(gt=0)
    choice_id: int | None = Field(default=None, gt=0)


class MultipartPartsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[MultipartPartWrite] = Field(max_length=100)


class MultipartModelSave(MultipartModelUpdate):
    """Atomic metadata and composition replacement."""

    collection_id: Optional[int] = None
    parts: list[MultipartPartWrite] = Field(default_factory=list, max_length=100)
    cover_model_id: Optional[int] = Field(default=None, gt=0)


class MultipartModelStarRead(BaseModel):
    multipart_model_id: int
    starred: bool
