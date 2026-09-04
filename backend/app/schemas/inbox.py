from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from app.db.models import InboxItemCompletion, InboxItemState, InboxSourceKind


class CaptureFieldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    origin: Literal["confirmed", "inferred"]


class CaptureSourceDraft(BaseModel):
    """Bounded, transport-free capture provenance supplied by a browser."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "makerworld", "printables", "thingiverse", "myminifactory", "cults"
    ]
    canonical_url: str = Field(min_length=1, max_length=2048)
    source_item_id: str | None = Field(default=None, max_length=255)
    source_revision: str | None = Field(default=None, max_length=255)
    adapter_version: str = Field(min_length=1, max_length=64)
    fields: dict[str, CaptureFieldDraft] = Field(default_factory=dict, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=100)


class InboxItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=255)
    source_kind: InboxSourceKind = InboxSourceKind.URL
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list, max_length=100)
    capture_source: CaptureSourceDraft | None = None


class CaptureUploadFileDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=512)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CaptureUploadSlotsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=2048)
    capture_source: CaptureSourceDraft
    title: str | None = Field(default=None, max_length=255)
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list, max_length=100)
    files: list[CaptureUploadFileDeclaration] = Field(min_length=1, max_length=100)
    cover: CaptureUploadFileDeclaration | None = None

    @field_validator("files")
    @classmethod
    def unique_file_ids(
        cls, value: list[CaptureUploadFileDeclaration]
    ) -> list[CaptureUploadFileDeclaration]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("duplicate_capture_file_id")
        return value


class CaptureUploadSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: Literal["file", "cover"]
    source_file_id: str | None = None
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    state: Literal["pending", "uploaded"]


class InboxItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] | None = Field(default=None, max_length=100)
    selected_ids: list[str] | None = Field(default=None, max_length=500)


class InboxImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_ids: list[str] = Field(default_factory=list, max_length=500)


class InboxBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ids: list[int] = Field(min_length=1, max_length=500)
    action: str
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        if value not in {"set_collection", "add_tags", "import", "retry", "dismiss"}:
            raise ValueError("unsupported_batch_action")
        return value


class InboxItemResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_selection_id: str
    result_key: str
    original_filename: str
    state: str
    model_id: int | None = None
    file_id: int | None = None
    provenance_source_id: int | None = None
    error_code: str | None = None
    retryable: bool
    created_at: datetime
    updated_at: datetime


class CaptureManifestFileRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    file_type: str
    size: int | None = None


class CaptureManifestV2Read(BaseModel):
    """Strict additive read shape for a finalized rich capture manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    kind: Literal["model_files"]
    source: CaptureSourceDraft
    files: list[CaptureManifestFileRead]
    selected_ids: list[str]


class LegacyInboxManifestRead(RootModel[dict[str, Any]]):
    """Compatibility wrapper for the pre-capture manifest variants."""

    @model_validator(mode="after")
    def reject_v2_fallback(self) -> LegacyInboxManifestRead:
        if self.root.get("schema_version") == 2:
            raise ValueError("invalid_v2_capture_manifest")
        return self


InboxManifestRead = CaptureManifestV2Read | LegacyInboxManifestRead


class InboxItemRead(BaseModel):
    id: int
    owner_user_id: int
    source_kind: InboxSourceKind
    source_url: str | None = None
    display_title: str | None = None
    source_hostname: str | None = None
    state: InboxItemState
    manifest: InboxManifestRead = Field(default_factory=LegacyInboxManifestRead)
    target_collection_id: int | None = None
    requested_tags: list[str] = Field(default_factory=list)
    background_job_id: str | None = None
    resulting_model_id: int | None = None
    results: list[InboxItemResultRead] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    completion: InboxItemCompletion | None = None


class CaptureUploadSlotsRead(BaseModel):
    item: InboxItemRead
    slots: list[CaptureUploadSlotRead]
