from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import CompatibilityPolicy, JobPriority, RoutingStrategy
from app.schemas.printers import PrintJobRead


class QueueJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(gt=0)
    strategy: RoutingStrategy = RoutingStrategy.MANUAL
    printer_id: Optional[int] = Field(default=None, gt=0)
    spool_id: Optional[int] = None
    spool_name: Optional[str] = Field(default=None, max_length=256)
    spool_filament_id: Optional[int] = None
    priority: JobPriority = JobPriority.NORMAL
    target_group: Optional[str] = Field(default=None, max_length=128)
    compatibility_policy: CompatibilityPolicy = CompatibilityPolicy.SAFE

    @model_validator(mode="after")
    def manual_requires_printer(self) -> "QueueJobCreate":
        if self.strategy == RoutingStrategy.MANUAL and self.printer_id is None:
            raise ValueError("printer_id_required")
        return self


class QueueJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Optional[RoutingStrategy] = None
    printer_id: Optional[int] = Field(default=None, gt=0)
    queue_position: Optional[int] = Field(default=None, ge=1)
    expected_updated_at: Optional[datetime] = None
    priority: Optional[JobPriority] = None
    target_group: Optional[str] = Field(default=None, max_length=128)
    compatibility_policy: Optional[CompatibilityPolicy] = None


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    strategy: RoutingStrategy = RoutingStrategy.LEAST_BUSY
    printer_id: Optional[int] = Field(default=None, gt=0)
    target_group: Optional[str] = Field(default=None, max_length=128)
    priority: JobPriority = JobPriority.NORMAL
    compatibility_policy: CompatibilityPolicy = CompatibilityPolicy.SAFE
    spool_id: Optional[int] = None
    spool_name: Optional[str] = Field(default=None, max_length=256)
    spool_filament_id: Optional[int] = None

    @model_validator(mode="after")
    def validate_target(self) -> "BatchCreate":
        if self.strategy == RoutingStrategy.MANUAL and self.printer_id is None:
            raise ValueError("printer_id_required")
        if self.strategy != RoutingStrategy.MANUAL and self.spool_id is not None:
            raise ValueError("automatic_batch_spool_not_allowed")
        return self


class PrintBatchRead(BaseModel):
    id: int
    file_id: int
    model_id: int
    quantity: int
    routing_strategy: RoutingStrategy
    priority: JobPriority
    target_group: Optional[str] = None
    compatibility_policy: CompatibilityPolicy
    requested_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    jobs: list[PrintJobRead] = Field(default_factory=list)


class OperatorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["release", "hold"]


class PrinterRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_default: Optional[bool] = None
    drain_mode: Optional[bool] = None
    drain_reason: Optional[str] = Field(default=None, max_length=512)


class PrinterRoutingRead(BaseModel):
    printer_id: int
    is_default: bool
    drain_mode: bool
    drain_reason: Optional[str] = None
    drain_updated_at: Optional[datetime] = None


class MaintenanceWindowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def valid_range(self) -> "MaintenanceWindowCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("maintenance_window_invalid")
        return self


class MaintenanceWindowRead(BaseModel):
    id: int
    printer_id: int
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MaintenanceWindowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=512)


class MaintenanceLogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: Optional[datetime] = None
    category: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=1, max_length=4096)
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = Field(default=None, max_length=32)


class MaintenanceLogRead(BaseModel):
    id: int
    printer_id: int
    performed_at: datetime
    category: str
    note: str
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MaintenanceLogUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed_at: Optional[datetime] = None
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    counter_value: Optional[float] = None
    counter_unit: Optional[str] = Field(default=None, max_length=32)


class FleetPrinterBoardRead(BaseModel):
    printer_id: int
    name: str
    status: str
    progress: float | None = None
    group: Optional[str] = None
    loaded_slots: list[str] = Field(default_factory=list)
    nozzle_diameter_mm: float | None = None
    current_job_id: int | None = None
    current_job_name: str | None = None
    current_priority: JobPriority | None = None
    next_job_id: int | None = None
    next_job_name: str | None = None
    next_priority: JobPriority | None = None
    drain_mode: bool = False
    maintenance: bool = False
    pending_operator_release: bool = False


class FleetSummary(BaseModel):
    total_printers: int
    queued_jobs: int
    active_jobs: int
    draining_printers: int
    maintenance_printers: int
    attention_jobs: int
    printers: list[FleetPrinterBoardRead] = Field(default_factory=list)
