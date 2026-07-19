from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRunState,
    VaultAuditSeverity,
)


class VaultAuditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: VaultAuditMode = VaultAuditMode.QUICK


class VaultAuditFindingRead(BaseModel):
    id: int
    run_id: int
    code: str
    severity: VaultAuditSeverity
    resource_type: str
    resource_identifier: str
    repair_action: str | None = None
    state: VaultAuditFindingState
    details: dict = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: int | None = None


class VaultAuditRunRead(BaseModel):
    id: int
    requested_by: int
    mode: VaultAuditMode
    state: VaultAuditRunState
    info_count: int
    warning_count: int
    critical_count: int
    progress: float
    current_phase: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    findings: list[VaultAuditFindingRead] = Field(default_factory=list)
