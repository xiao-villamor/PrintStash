"""Human variation relationships and authorized Family projections."""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import ensure_utc
from app.db.models import CollectionRole
from app.schemas.family_types import MemberRole, VariantRole
from app.schemas.models import FileRead, ModelListItem


class FamilyMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: int = Field(gt=0)
    role: MemberRole = "identical"
    transformation_note: str | None = Field(default=None, max_length=4096)
    scale_factor: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mirrored: bool = False
    mirror_verified: bool = False
    sort_order: int = Field(default=0, ge=0, le=1_000_000)


class FamilyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1_000_000)
    collection_id: int | None = Field(default=None, gt=0)
    canonical_model_id: int = Field(gt=0)
    members: list[FamilyMemberInput] = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("family_name_required")
        return value.strip()

    @model_validator(mode="after")
    def distinct_members_with_canonical(self) -> "FamilyCreate":
        ids = [member.model_id for member in self.members]
        if len(set(ids)) != len(ids):
            raise ValueError("family_duplicate_members")
        if self.canonical_model_id not in ids:
            raise ValueError("family_canonical_invalid")
        return self


class FamilyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(gt=0)


class FamilyUpdate(FamilyVersion):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1_000_000)
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] | None = Field(default=None, max_length=100)
    cover_model_id: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("family_name_required")
        return value.strip()


class FamilyMemberAdd(FamilyMemberInput):
    version: int = Field(gt=0)


class FamilyMemberUpdate(FamilyVersion):
    role: MemberRole | None = None
    transformation_note: str | None = Field(default=None, max_length=4096)
    scale_factor: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    mirrored: bool | None = None
    mirror_verified: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("role", "mirrored", "mirror_verified", "sort_order")
    @classmethod
    def supplied_values_are_nonnull(cls, value):
        if value is None:
            raise ValueError("family_member_value_required")
        return value


class FamilyCanonicalChange(FamilyVersion):
    member_id: int = Field(gt=0)
    previous_role: MemberRole


class FamilyMemberMove(FamilyMemberInput):
    source_family_id: int = Field(gt=0)
    source_version: int = Field(gt=0)
    destination_version: int = Field(gt=0)


class FamilyRead(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None = None
    collection_id: int | None = None
    collection: str | None = None
    version: int
    canonical_model_id: int | None = None
    canonical_member_id: int | None = None
    cover_model_id: int | None = None
    cover_thumbnail_url: str | None = None
    cover_image_uploaded: bool = False
    member_count: int = 0
    total_visible_members: int = 0
    matching_visible_members: int = 0
    tags: list[str] = Field(default_factory=list)
    starred: bool = False
    effective_role: CollectionRole = CollectionRole.VIEW
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def utc_dates(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class FamilyMemberRead(BaseModel):
    id: int
    model_id: int
    role: VariantRole
    transformation_note: str | None = None
    scale_factor: float | None = None
    mirrored: bool = False
    mirror_verified: bool = False
    relative_review_required: bool = False
    joined_via: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_dates(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class FamilyRestoreRead(BaseModel):
    family: FamilyRead
    omitted_member_ids: list[int] = Field(default_factory=list)


class FamilyPageRead(BaseModel):
    items: list[FamilyRead]
    total: int
    next_cursor: str | None = None


class FamilyBrowseCard(BaseModel):
    kind: Literal["family"] = "family"
    family: FamilyRead


class ModelBrowseCard(BaseModel):
    kind: Literal["model"] = "model"
    model: ModelListItem


class FamilyBrowsePage(BaseModel):
    items: list[FamilyBrowseCard | ModelBrowseCard]
    total: int
    next_cursor: str | None = None


class FamilyMemberSort(str, Enum):
    ORDER = "order"
    SCALE_ASC = "scale-asc"
    SCALE_DESC = "scale-desc"
    DATE_ASC = "date-asc"
    DATE_DESC = "date-desc"
    SUCCESS_DESC = "success-desc"


class FamilyMemberItem(FamilyMemberRead):
    model: ModelListItem
    preview_file: FileRead | None = None
    formats: list[str] = Field(default_factory=list)
    source_file_count: int = 0
    gcode_revision_count: int = 0
    known_good_count: int = 0
    latest_print_outcome: str | None = None
    units: Literal["mm", "unknown"] = "unknown"


class FamilyMemberPage(BaseModel):
    items: list[FamilyMemberItem]
    total: int
    next_cursor: str | None = None
