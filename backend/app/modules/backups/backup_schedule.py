"""Durable daily scheduling for opt-in automatic vault backups."""

from __future__ import annotations

from datetime import datetime, time

from sqlmodel import Session

from app.core.time import ensure_utc, parse_hh_mm_time, utcnow
from app.db.models import SystemConfig
from app.modules.administration.config_repository import get_or_create

DEFAULT_BACKUP_TIME_UTC = "02:00"


def parse_backup_time(value: str) -> time:
    try:
        return parse_hh_mm_time(value)
    except ValueError as exc:
        raise ValueError("automatic_backup_time_invalid") from exc


def automatic_backup_due(config: SystemConfig | None, now: datetime) -> bool:
    if config is None or not config.automatic_backups_enabled:
        return False
    now = ensure_utc(now)
    scheduled = parse_backup_time(config.automatic_backup_time_utc)
    if now.time().replace(tzinfo=None) < scheduled:
        return False
    previous = config.automatic_backup_last_attempt_at
    return previous is None or ensure_utc(previous).date() < now.date()


def claim_due_backup(session: Session, *, now: datetime | None = None) -> bool:
    attempted_at = ensure_utc(now or utcnow())
    config = session.get(SystemConfig, 1)
    if not automatic_backup_due(config, attempted_at):
        return False
    assert config is not None
    config.automatic_backup_last_attempt_at = attempted_at
    session.add(config)
    session.commit()
    return True


def update_schedule(
    session: Session,
    *,
    enabled: bool | None = None,
    time_utc: str | None = None,
    manual_local_enabled: bool | None = None,
    automatic_local_enabled: bool | None = None,
) -> SystemConfig:
    config = get_or_create(session, commit=False)
    if time_utc is not None:
        parse_backup_time(time_utc)
        config.automatic_backup_time_utc = time_utc
    if enabled is not None:
        config.automatic_backups_enabled = enabled
    if manual_local_enabled is not None:
        config.manual_local_backup_enabled = manual_local_enabled
    if automatic_local_enabled is not None:
        config.automatic_local_backup_enabled = automatic_local_enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config
