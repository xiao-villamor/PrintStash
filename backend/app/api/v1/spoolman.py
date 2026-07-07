"""Spoolman integration — superuser config + read-only inventory proxy.

The connection config carries an optional secret (API key); reads mask it and
updates preserve a stored secret when re-sent blank, mirroring the S3/MakerWorld
secret handling in :mod:`app.api.v1.config`. Every read endpoint is gated on the
master switch and degrades gracefully — a disabled or unreachable Spoolman never
errors the request hard.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from app.core.logging import get_logger
from app.core.security import require_superuser
from app.db.session import get_session
from app.services import filament_sync, runtime_config
from app.services.runtime_config import _UNSET
from app.services.spoolman import SpoolmanClient, SpoolmanError, get_spoolman_client

logger = get_logger(__name__)

router = APIRouter(prefix="/spoolman", tags=["spoolman"])

# Placeholder the UI sends back for an unchanged secret (matches config.py).
_SECRET_MASK = "********"


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #
class SpoolmanStatus(BaseModel):
    enabled: bool = False
    base_url: Optional[str] = None
    has_api_key: bool = False
    write_enabled: bool = True
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
async def get_status(session: Session = Depends(get_session)) -> SpoolmanStatus:
    config = runtime_config.spoolman_config(session)
    enabled = runtime_config.spoolman_enabled(session)
    out = SpoolmanStatus(
        enabled=enabled,
        base_url=config.get("base_url"),
        has_api_key=bool(config.get("api_key")),
        write_enabled=runtime_config.spoolman_write_enabled(session),
        write_force=runtime_config.spoolman_write_force(session),
    )
    # Only hit the network when there's something to probe.
    if enabled and config.get("base_url"):
        probe = await _probe(SpoolmanClient(config["base_url"], config.get("api_key")))
        out.connected = probe["connected"]
        out.version = probe["version"]
        out.error = probe["error"]
        out.native_hook_detected = probe["native_hook_detected"]
    return out


@router.put(
    "",
    dependencies=[Depends(require_superuser)],
    summary="Update Spoolman connection + toggles",
)
async def update_status(
    body: SpoolmanUpdate, session: Session = Depends(get_session)
) -> SpoolmanStatus:
    if body.base_url is not None or body.api_key is not None:
        # Preserve the stored key when the UI re-sends the mask or a blank;
        # `_UNSET` leaves a field untouched, an explicit value (incl. "") sets it.
        api_key = _UNSET if body.api_key in (None, _SECRET_MASK) else body.api_key
        runtime_config.set_spoolman_config(
            session,
            base_url=body.base_url if body.base_url is not None else _UNSET,
            api_key=api_key,
        )
    if body.write_enabled is not None:
        runtime_config.set_spoolman_write_enabled(session, body.write_enabled)
    if body.write_force is not None:
        runtime_config.set_spoolman_write_force(session, body.write_force)
    if body.enabled is not None:
        was_enabled = runtime_config.spoolman_enabled(session)
        runtime_config.set_spoolman_enabled(session, body.enabled)
        # Pull filaments once on enable so presets reflect Spoolman immediately.
        # Best-effort: a failure here must not fail the settings save.
        if body.enabled and not was_enabled:
            try:
                await filament_sync.sync_from_spoolman(session)
            except SpoolmanError as exc:
                logger.warning("initial Spoolman filament sync skipped: %s", exc)
    return await get_status(session)


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
    base_url = (body.base_url if body and body.base_url else None) or config.get("base_url")
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
