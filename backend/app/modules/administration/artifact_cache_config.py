"""Administrative cache policy and usage without exposing cached object paths."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.core.config import _overlay, settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import LibraryRevision, SystemConfig, User
from app.db.transactions import rollback_on_failure
from app.modules.administration import config_edits
from app.modules.derivatives import policy as derivative_policy
from app.modules.storage.artifact_materializer import CachePolicy, CacheUnavailable
from app.modules.storage.materializer_runtime import (
    get_materializer,
    get_materializer_root,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.schemas.editing import EditingBase


def validate_cache_root(value: str | Path) -> Path:
    root = Path(value).expanduser().absolute()
    resolved = root.resolve(strict=False)
    for name in ("data_dir", "thumb_dir", "staging_dir", "backup_dir"):
        protected = Path(getattr(settings, name)).expanduser().resolve(strict=False)
        if (
            resolved == protected
            or resolved in protected.parents
            or protected in resolved.parents
        ):
            raise ValueError("cache_root_overlaps_managed_storage")
    return root


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    root: str = Field(min_length=1, max_length=1024)
    max_bytes: int = Field(ge=0)
    max_entries: int = Field(ge=0, le=1000000)
    max_fills: int = Field(ge=1, le=64)
    headroom_bytes: int = Field(ge=0)
    verify_every_hits: int = Field(ge=0)
    fill_wait_seconds: int = Field(default=30, ge=0, le=300)


class CacheSettingsRead(EditingBase):
    policy: CacheSettings
    effective_root: str
    restart_required: bool
    source: str
    available: bool
    health: str
    labels: dict[str, str]
    usage: dict[str, int]


def live_policy() -> CachePolicy:
    return CachePolicy(
        **{
            name: getattr(settings, f"artifact_cache_{name}")
            for name in asdict(CachePolicy())
        }
    )


def apply_cache_overlay(config: SystemConfig) -> None:
    if config.artifact_cache_policy_json:
        policy = CacheSettings.model_validate_json(config.artifact_cache_policy_json)
        for name, value in policy.model_dump().items():
            _overlay[f"artifact_cache_{name}"] = value


def read_settings(session: Session) -> CacheSettingsRead:
    config, epoch = session.exec(
        select(SystemConfig, LibraryRevision.epoch)
        .select_from(LibraryRevision)
        .outerjoin(SystemConfig, col(SystemConfig.id) == 1)
        .where(col(LibraryRevision.id) == 1)
        .execution_options(populate_existing=True)
    ).one()
    policy = (
        CacheSettings.model_validate_json(config.artifact_cache_policy_json)
        if config and config.artifact_cache_policy_json
        else CacheSettings(
            enabled=settings.frozen.artifact_cache_enabled,
            root=str(settings.frozen.artifact_cache_root),
            max_bytes=settings.frozen.artifact_cache_max_bytes,
            max_entries=settings.frozen.artifact_cache_max_entries,
            max_fills=settings.frozen.artifact_cache_max_fills,
            headroom_bytes=settings.frozen.artifact_cache_headroom_bytes,
            verify_every_hits=settings.frozen.artifact_cache_verify_every_hits,
            fill_wait_seconds=settings.frozen.artifact_cache_fill_wait_seconds,
        )
    )
    cache = get_materializer()
    available = cache is not None
    usage: dict[str, int] = {}
    if cache:
        try:
            usage = cache.status()
        except CacheUnavailable, OSError, sqlite3.Error:
            available = False
    effective_root = str(get_materializer_root() or settings.artifact_cache_root)
    backend_id = str(get_backend().provider_id)
    return CacheSettingsRead(
        edit_epoch=epoch,
        edit_version=1 if config is None else config.vault_edit_version,
        policy=policy,
        effective_root=effective_root,
        restart_required=(
            Path(policy.root).absolute() != Path(effective_root).absolute()
            or (policy.enabled and cache is None)
        ),
        source="database"
        if config and config.artifact_cache_policy_json
        else "environment",
        available=available,
        labels={
            "representation": "artifact",
            "backend": backend_id
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,48}", backend_id)
            else "unknown",
        },
        health=str(cache.health()["state"])
        if cache
        else ("unavailable" if policy.enabled else "disabled"),
        usage=usage,
    )


def update_settings(
    session: Session,
    policy: CacheSettings | None,
    *,
    actor: User,
    base: EditingBase | None,
) -> CacheSettingsRead:
    with rollback_on_failure(session):
        config = derivative_policy.lock(session)
        config_edits.claim(session, actor, config, base)
        if policy is not None:
            try:
                validate_cache_root(policy.root)
            except ValueError as exc:
                raise OperationError(
                    "cache_root_overlaps_managed_storage", kind=ErrorKind.INVALID
                ) from exc
        config.artifact_cache_policy_json = policy.model_dump_json() if policy else None
        session.add(config)
        session.flush()
        response = read_settings(session)
        session.commit()
        # Publish the latest committed policy, even if this publisher was delayed.
        current = derivative_policy.lock(session)
        try:
            for name in CacheSettings.model_fields:
                _overlay.pop(f"artifact_cache_{name}", None)
            apply_cache_overlay(current)
        finally:
            session.rollback()
        cache = get_materializer()
        if cache is not None:
            cache.request_maintenance()
            response.usage["maintenance_running"] = 1
        return response


def clear_cache(session: Session) -> CacheSettingsRead:
    cache = get_materializer()
    if cache:
        cache.clear()
    return read_settings(session)
