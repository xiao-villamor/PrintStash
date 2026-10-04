from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.time import ensure_utc, parse_hh_mm_time, utcnow
from app.db.models import VaultAuditPolicy


class AuditPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(default=None, ge=1)
    jitter_seconds: int = Field(default=0, ge=0, le=3600)
    max_lateness_minutes: int = Field(default=120, ge=1, le=44640)
    notification_threshold: Literal["off", "critical", "warning", "info"] = "warning"
    notification_channels: list[int] = Field(default_factory=list, max_length=100)
    notification_cooldown_minutes: int = Field(default=60, ge=0, le=1440)
    enabled: bool = False
    paused: bool = False
    cadence: Literal["weekly", "monthly"] = "weekly"
    timezone: str = "UTC"
    weekday: int = Field(default=6, ge=0, le=6)
    month_day: int = Field(default=1, ge=1, le=31)
    start_time: str = "02:00"
    window_minutes: int = Field(default=120, ge=1, le=1440)
    bytes_per_second: int = Field(default=10485760, ge=1024, le=1073741824)
    read_concurrency: int = Field(default=1, ge=1, le=1)
    auto_repair: bool = False
    repair_actions: list[Literal["reparse_metadata", "regenerate_thumbnail"]] = Field(
        default_factory=list
    )
    full_cost_acknowledged: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("audit_timezone_invalid") from exc
        return value

    @field_validator("start_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        try:
            parse_hh_mm_time(value)
        except ValueError as exc:
            raise ValueError("audit_time_invalid") from exc
        return value


class AuditPolicyRead(AuditPolicyUpdate):
    estimated_remote_bytes: int = 0
    overdue: bool = False
    mode: str
    revision: int
    next_due_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    deferred_reason: str | None


def policy_read(
    row: VaultAuditPolicy, *, estimated_remote_bytes: int = 0
) -> AuditPolicyRead:
    values = row.model_dump(
        exclude={
            "repair_actions_json",
            "notification_channels_json",
            "last_notified_at",
            "retry_after",
            "launch_failures",
            "requested_by",
            "updated_at",
        }
    )
    return AuditPolicyRead(
        **values,
        repair_actions=json.loads(row.repair_actions_json),
        notification_channels=json.loads(row.notification_channels_json),
        estimated_remote_bytes=estimated_remote_bytes,
        overdue=bool(
            row.enabled
            and not row.paused
            and row.next_due_at is not None
            and utcnow()
            > ensure_utc(row.next_due_at) + timedelta(minutes=row.max_lateness_minutes)
        ),
    )
