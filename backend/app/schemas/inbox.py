from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import InboxItemState, InboxSourceKind


class InboxItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=255)
    source_kind: InboxSourceKind = InboxSourceKind.URL
    collection_id: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list, max_length=100)


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


class InboxItemRead(BaseModel):
    id: int
    owner_user_id: int
    source_kind: InboxSourceKind
    source_url: str | None = None
    display_title: str | None = None
    source_hostname: str | None = None
    state: InboxItemState
    manifest: dict = Field(default_factory=dict)
    target_collection_id: int | None = None
    requested_tags: list[str] = Field(default_factory=list)
    background_job_id: str | None = None
    resulting_model_id: int | None = None
    error_code: str | None = None
    retryable: bool
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
