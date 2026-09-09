"""Safe public state for administrative Vault migration controls."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MigrationPolicy(BaseModel):
    retention_days: int = Field(default=7, ge=0, le=3650)
    concurrency: int = Field(default=1, ge=1, le=4)
    bandwidth_bytes_per_second: int | None = Field(default=None, ge=1024)


class MigrationCleanupFinding(BaseModel):
    object_id: int | None
    code: str


class MigrationBackupSummary(BaseModel):
    backup_id: str
    source_ref: str | None
    archive_sha256: str
    vault_target_ref: str
    created_at: datetime
    verified_at: datetime


class MigrationRunRead(BaseModel):
    id: str
    state: Literal[
        "planned",
        "copying",
        "ready",
        "paused",
        "cutover_pending",
        "draining",
        "delta_copy",
        "verifying",
        "activating",
        "active",
        "cleaned",
        "discarded",
        "failed",
        "recovery_required",
        "complete",
    ]
    plan_digest: str
    backup_summary: MigrationBackupSummary
    source: dict[str, object]
    destination: dict[str, object]
    source_provider_ref: str
    destination_provider_ref: str
    objects: int = Field(ge=0)
    verified_objects: int = Field(ge=0)
    bytes: int = Field(ge=0)
    copied_objects: int
    copied_bytes: int
    verified_bytes: int
    skipped_objects: int
    skipped_bytes: int
    failed_objects: int
    failed_bytes: int
    delta_objects: int
    throughput_bytes_per_second: float
    last_activity_at: datetime
    retryable: bool
    policy: MigrationPolicy
    phase_history: list[dict[str, str]]
    pre_audit: dict[str, object] | None
    post_audit: dict[str, object] | None
    full_audit: dict[str, object] | None
    notification_events: int
    error_code: str | None
    expires_at: datetime
    cleanup_after: datetime | None
    cleanup_findings: list[MigrationCleanupFinding]
    cleanup_outcome: str | None
    source_retained: bool
    recovery_required: bool
    capacity_warnings: list[str]
    capacity_resources: list[dict[str, object]]
    resource_kind_totals: list[dict[str, object]]
    recent_failures: list[dict[str, object]]
