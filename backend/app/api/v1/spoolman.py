"""Spoolman integration — superuser config + read-only inventory proxy.

The connection config carries an optional secret (API key); reads mask it and
updates preserve a stored secret when re-sent blank, mirroring the S3/MakerWorld
secret handling in :mod:`app.api.v1.config`. Every read endpoint is gated on the
master switch and degrades gracefully — a disabled or unreachable Spoolman never
errors the request hard.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.api.edit_preconditions import edit_precondition
from app.core.logging import get_logger
from app.core.security import require_superuser
from app.db.models import User
from app.db.session import get_session
from app.modules.administration import config_repository, runtime_config
from app.modules.printing import filament_sync, spoolman_editing
from app.modules.printing.spoolman import (
    SpoolmanClient,
    SpoolmanError,
    get_spoolman_client,
)
from app.schemas.editing import EditingBase, EditPrecondition

logger = get_logger(__name__)

router = APIRouter(prefix="/spoolman", tags=["spoolman"])

# Placeholder the UI sends back for an unchanged secret (matches config.py).
_SECRET_MASK = "********"


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #
class SpoolmanStatus(EditingBase):
    enabled: bool = False
    base_url: Optional[str] = None
    has_api_key: bool = False
    write_enabled: bool = False
    # Override the native-hook double-count guard (write back even when Spoolman
    # reports an active spool). Off by default.
    write_force: bool = False
    # Filled in by a live probe when enabled + configured.
    connected: bool = False
    version: Optional[str] = None
    error: Optional[str] = None
    # True when Moonraker's native Spoolman hook is already decrementing the
    # active spool; the write path skips its own decrement (unless write_force)
    # and the UI warns about it.
    native_hook_detected: bool = False


class SpoolmanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    write_enabled: Optional[bool] = None
    write_force: Optional[bool] = None


class SpoolmanTestRequest(BaseModel):
    """Optional overrides so the UI can test a typed-but-unsaved connection."""

    model_config = ConfigDict(extra="forbid")

    base_url: Optional[str] = None
    api_key: Optional[str] = None


class SpoolRead(BaseModel):
    id: int
    filament_id: Optional[int] = None
    name: Optional[str] = None
    filament_name: Optional[str] = None
    vendor_name: Optional[str] = None
    material: Optional[str] = None
    color_hex: Optional[str] = None
    remaining_weight: Optional[float] = None
    used_weight: Optional[float] = None
    archived: bool = False
    # Spoolman's free-text slot/bin label — the only thing that distinguishes
    # otherwise-identical spools (same vendor/material/color) in a multi-slot
    # changer (AMS, CANVAS, MMU).
    location: Optional[str] = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _spool_from_spoolman(raw: Dict[str, Any]) -> SpoolRead:
    """Flatten a Spoolman spool record into our display schema.

    Spoolman nests filament → vendor; we surface the few fields the UI shows.
    """
    filament = raw.get("filament") or {}
    vendor = filament.get("vendor") or {}
    return SpoolRead(
        id=int(raw.get("id")),
        filament_id=filament.get("id"),
        name=filament.get("name") or raw.get("name"),
        filament_name=filament.get("name"),
        vendor_name=vendor.get("name"),
        material=filament.get("material"),
        color_hex=filament.get("color_hex"),
        remaining_weight=raw.get("remaining_weight"),
        used_weight=raw.get("used_weight"),
        archived=bool(raw.get("archived", False)),
        location=raw.get("location"),
    )


async def _probe(client: SpoolmanClient) -> Dict[str, Any]:
    """Reachability + native-hook probe. Never raises."""
    out: Dict[str, Any] = {
        "connected": False,
        "version": None,
        "error": None,
        "native_hook_detected": False,
    }
    try:
        info = await client.health_check()
        out["connected"] = True
        out["version"] = info.get("version") if isinstance(info, dict) else None
        out["native_hook_detected"] = await client.active_spool() is not None
    except SpoolmanError as exc:
        out["error"] = str(exc)
    return out


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
@router.get(
    "",
    dependencies=[Depends(require_superuser)],
    summary="Spoolman connection status + config",
)
async def get_status(
    response: Response, session: Session = Depends(get_session)
) -> SpoolmanStatus:
    result, key = _status_snapshot(session)
    response.headers["ETag"] = spoolman_editing.etag(result)
    return await _probe_status(result, key)


def _status_snapshot(session: Session) -> tuple[SpoolmanStatus, str | None]:
    base, config = spoolman_editing.read(session)
    result = SpoolmanStatus(
        **base.model_dump(),
        enabled=False if config is None else config.spoolman_enabled,
        base_url=None if config is None else config.spoolman_base_url,
        has_api_key=bool(config and config.spoolman_api_key),
        write_enabled=False if config is None else config.spoolman_write_enabled,
        write_force=False if config is None else config.spoolman_write_force,
    )
    return result, None if config is None else config.spoolman_api_key


async def _probe_status(result: SpoolmanStatus, key: str | None) -> SpoolmanStatus:
    if result.enabled and result.base_url:
        probe = await _probe(SpoolmanClient(result.base_url, key))
        result.connected = probe["connected"]
        result.version = probe["version"]
        result.error = probe["error"]
        result.native_hook_detected = probe["native_hook_detected"]
    return result


@router.put(
    "",
    dependencies=[Depends(require_superuser)],
    summary="Update Spoolman connection + toggles",
)
async def update_status(
    body: SpoolmanUpdate,
    response: Response,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
    precondition: EditPrecondition = Depends(edit_precondition),
) -> SpoolmanStatus:
    base = spoolman_editing.expected_base(precondition)
    config = config_repository.get_or_create(session, commit=False)
    session.flush()
    spoolman_editing.claim(session, actor, config, base)
    was_enabled = config.spoolman_enabled
    if body.base_url is not None or body.api_key is not None:
        changes = {}
        if body.base_url is not None:
            changes["base_url"] = body.base_url
        if body.api_key not in (None, _SECRET_MASK):
            changes["api_key"] = body.api_key
        runtime_config.set_spoolman_config(session, **changes, commit=False)
    if body.write_enabled is not None:
        runtime_config.set_spoolman_write_enabled(
            session, body.write_enabled, commit=False
        )
    if body.write_force is not None:
        runtime_config.set_spoolman_write_force(session, body.write_force, commit=False)
    if body.enabled is not None:
        runtime_config.set_spoolman_enabled(session, body.enabled, commit=False)
    result, key = _status_snapshot(session)
    session.commit()
    response.headers["ETag"] = spoolman_editing.etag(result)
    # Preserve the existing enable-time synchronization contract after the atomic save.
    if body.enabled and not was_enabled:
        try:
            await filament_sync.sync_from_spoolman(session)
        except SpoolmanError as exc:
            logger.warning("initial Spoolman filament sync skipped: %s", exc)
    return await _probe_status(result, key)


@router.post(
    "/sync-filaments",
    dependencies=[Depends(require_superuser)],
    summary="Import/refresh local filament presets from Spoolman",
)
async def sync_filaments(session: Session = Depends(get_session)) -> Dict[str, Any]:
    try:
        result = await filament_sync.sync_from_spoolman(session)
    except SpoolmanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return result.as_dict()


@router.post(
    "/test",
    dependencies=[Depends(require_superuser)],
    summary="Test the configured Spoolman connection",
)
async def test_connection(
    body: Optional[SpoolmanTestRequest] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    # Test the values typed into the form when provided, so the user can verify
    # a connection before committing it with Save. Fall back to the stored
    # config (and preserve the saved key when the UI re-sends the mask/blank).
    config = runtime_config.spoolman_config(session)
    base_url = (body.base_url if body and body.base_url else None) or config.get(
        "base_url"
    )
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Spoolman base URL is not configured",
        )
    api_key = config.get("api_key")
    if body and body.api_key not in (None, _SECRET_MASK):
        api_key = body.api_key
    return await _probe(SpoolmanClient(base_url, api_key))


# --------------------------------------------------------------------------- #
# inventory (read-only proxy)
# --------------------------------------------------------------------------- #
@router.get(
    "/spools",
    dependencies=[Depends(require_superuser)],
    summary="Spoolman spool inventory",
)
async def list_spools(
    include_archived: bool = False, session: Session = Depends(get_session)
) -> List[SpoolRead]:
    if not runtime_config.spoolman_enabled(session):
        return []
    try:
        client = get_spoolman_client(session)
        spools = await client.list_spools(include_archived=include_archived)
    except SpoolmanError as exc:
        # Graceful degradation: a Spoolman outage yields an empty list, not a 500.
        logger.warning("Spoolman spool list unavailable: %s", exc)
        return []
    return [_spool_from_spoolman(s) for s in spools if s.get("id") is not None]
