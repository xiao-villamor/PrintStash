"""Manufacturing contracts: quantities are units, never implicit job counts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import CollectionRole
from app.schemas.fleet import BatchRouting


class BuildCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    multipart_model_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    object_quantity: int = Field(default=1, ge=1, le=10_000)


class BuildVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)


class BuildSelection(BuildVersion):
    choice_id: int | None = Field(default=None, gt=0)
    model_id: int | None = Field(default=None, gt=0)
    revision_id: int | None = Field(default=None, gt=0)


class BuildQueue(BuildVersion):
    units_per_job: int = Field(default=1, ge=1, le=10_000)
    # Omitted means propose all unreserved missing units.
    job_count: int | None = Field(default=None, ge=1, le=1000)
    confirm_excess: bool = False
    routing: BatchRouting = Field(default_factory=BatchRouting)


class BuildConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    valid_units: int = Field(ge=0, le=10_000)


class BuildDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)


class BuildArchive(BuildVersion):
    archived: bool = True


class BuildChoiceRead(BaseModel):
    choice_id: int | None
    model_id: int
    name: str | None = None
    available: bool


class BuildAttemptRead(BaseModel):
    id: int
    job_id: int | None
    historical_job_id: int
    revision_id: int | None
    planned_units: int
    valid_units: int | None
    suggested_valid_units: int
    state: str
    version: int


class BuildPartRead(BaseModel):
    id: int
    name: str
    quantity: int
    required_units: int
    valid_units: int
    missing_units: int
    active_units: int
    unreviewed_units: int
    unreserved_units: int
    selected_model_id: int | None
    selected_choice_id: int | None
    revision_id: int | None
    queueable: bool
    choices: list[BuildChoiceRead]
    attempts: list[BuildAttemptRead]


class BuildRead(BaseModel):
    effective_role: CollectionRole | None
    id: int
    name: str
    multipart_model_id: int | None
    composition_name: str
    object_quantity: int
    version: int
    archived_at: datetime | None
    created_at: datetime
    completed: bool
    parts: list[BuildPartRead]
