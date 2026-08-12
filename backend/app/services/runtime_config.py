"""Runtime overlay for all configurable settings.

Writes overrides to the shared ``_overlay`` dict (defined in ``app.core.config``)
instead of mutating the ``settings`` singleton. The ``ConfigResolver`` reads
``_overlay`` on every attribute access, so all 16+ call sites see the effective
value without code changes. See ADR-0002.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.core.config import DEFAULT_JWT_SECRET, _overlay, ensure_dirs, settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import SystemConfig, User

logger = get_logger(__name__)

# Sentinel for "argument not provided" so callers can leave a field untouched
# while still being allowed to pass an explicit ``None`` to clear it.
_UNSET: Any = object()


def get_or_create(session: Session, *, commit: bool = True) -> SystemConfig:
    """Return the singleton config row, creating an empty one if missing."""
    config = session.get(SystemConfig, 1)
    if config is None:
        config = SystemConfig(id=1)
        session.add(config)
        if commit:
            session.commit()
            session.refresh(config)
    return config


def get_config(session: Session) -> SystemConfig:
    return get_or_create(session)


def is_configured(session: Session) -> bool:
    """True once the setup wizard has run *and* at least one user exists.

    Both checks matter: if the DB is wiped but a stale ``system_config`` row
    survives somehow, we still surface the wizard.
    """
    config = session.get(SystemConfig, 1)
    if config is None or config.configured_at is None:
        return False
    has_user = session.exec(select(User.id).limit(1)).first() is not None
    return has_user


def _merge_config_overlay(config: SystemConfig) -> None:
    def _set(key: str, value) -> None:
        if value is not None and value != "":
            _overlay[key] = value

    if config.data_dir:
        _overlay["data_dir"] = Path(config.data_dir)
    if config.thumb_dir:
        _overlay["thumb_dir"] = Path(config.thumb_dir)
    _set("storage_backend", config.storage_backend)
    _set("s3_bucket", config.s3_bucket)
    _set("s3_endpoint_url", config.s3_endpoint_url)
    _set("s3_region", config.s3_region)
    _set("s3_access_key", config.s3_access_key)
    _set("s3_secret_key", config.s3_secret_key)
    if config.backup_retention_days is not None:
        _overlay["backup_retention_days"] = config.backup_retention_days
    if config.trash_retention_days is not None:
        _overlay["trash_retention_days"] = config.trash_retention_days
    _set("backup_s3_bucket", config.backup_s3_bucket)
    _set("backup_s3_endpoint_url", config.backup_s3_endpoint_url)
    _set("backup_s3_region", config.backup_s3_region)
    _set("backup_s3_access_key", config.backup_s3_access_key)
    _set("backup_s3_secret_key", config.backup_s3_secret_key)
    if config.oidc_enabled is not None:
        _overlay["oidc_enabled"] = config.oidc_enabled
    _set("oidc_issuer_url", config.oidc_issuer_url)
    _set("oidc_client_id", config.oidc_client_id)
    _set("oidc_client_secret", config.oidc_client_secret)
    _set("oidc_scopes", config.oidc_scopes)
    _set("oidc_username_claim", config.oidc_username_claim)
    _set("oidc_groups_claim", config.oidc_groups_claim)
    _set("oidc_admin_groups", config.oidc_admin_groups)
    _set("oidc_display_name", config.oidc_display_name)
    _set("oidc_redirect_uri", config.oidc_redirect_uri)
    if config.oidc_allow_insecure_http is not None:
        _overlay["oidc_allow_insecure_http"] = config.oidc_allow_insecure_http
    # A logged-in MakerWorld token overlays the env cookie as ``token=<jwt>`` so
    # the importer's existing cookie path picks it up (see makerworld_auth).
    if config.makerworld_token:
        _overlay["makerworld_cookie"] = f"token={config.makerworld_token}"


def apply_overlay(session: Session) -> None:
    """Replace runtime overrides with values persisted in ``system_config``."""
    config = session.get(SystemConfig, 1)
    if config is None:
        return
    _overlay.clear()
    _merge_config_overlay(config)


def activate_config(config: SystemConfig) -> None:
    """Merge a newly committed config into live runtime state."""
    _merge_config_overlay(config)
    ensure_dirs()


def ensure_jwt_secret(session: Session) -> None:
    """Guarantee this install does not sign tokens with the published default.

    Env wins and is never copied into the DB. Otherwise the persisted secret is
    applied, or a fresh one is generated and stored so it survives restarts —
    regenerating on every boot would log everyone out on every restart.

    Refusing to boot instead would brick every existing install, since the
    compose files default ``VAULT_JWT_SECRET`` to the shipped value.
    """
    if settings.jwt_secret != DEFAULT_JWT_SECRET:
        return

    config = get_or_create(session)
    if config.jwt_secret:
        _overlay["jwt_secret"] = config.jwt_secret
        return

    generated = secrets.token_hex(32)
    config.jwt_secret = generated
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    _overlay["jwt_secret"] = generated
    logger.warning(
        "no VAULT_JWT_SECRET set — generated one and stored it in the database. "
        "Existing login sessions are now invalid. Set VAULT_JWT_SECRET "
        "(openssl rand -hex 32) to manage it yourself."
    )


def update_storage(
    session: Session,
    *,
    data_dir: Optional[str] = None,
    thumb_dir: Optional[str] = None,
) -> SystemConfig:
    """Persist storage overrides into DB + overlay dict, then mkdir."""
    config = get_or_create(session)
    if data_dir is not None:
        config.data_dir = data_dir
        _overlay["data_dir"] = Path(data_dir)
    if thumb_dir is not None:
        config.thumb_dir = thumb_dir
        _overlay["thumb_dir"] = Path(thumb_dir)
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    ensure_dirs()
    logger.info(
        "runtime storage updated: data_dir=%s thumb_dir=%s",
        settings.data_dir,
        settings.thumb_dir,
    )
    return config


def _env_or_default(field_name: str) -> object:
    """Return the effective env-var value for *field_name* or its model default."""
    import os

    from app.core.config import Settings

    env_key = f"VAULT_{field_name.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    field = Settings.model_fields.get(field_name)
    if field is not None and field.default is not None:
        return field.default
    return ""


def update_config(
    session: Session,
    *,
    storage_backend: Optional[str] = None,
    data_dir: Optional[str] = None,
    thumb_dir: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    s3_endpoint_url: Optional[str] = None,
    s3_region: Optional[str] = None,
    s3_access_key: Optional[str] = None,
    s3_secret_key: Optional[str] = None,
    backup_retention_days: Optional[int] = None,
    trash_retention_days: Optional[int] = None,
    backup_s3_bucket: Optional[str] = None,
    backup_s3_endpoint_url: Optional[str] = None,
    backup_s3_region: Optional[str] = None,
    backup_s3_access_key: Optional[str] = None,
    backup_s3_secret_key: Optional[str] = None,
    oidc_enabled: Optional[bool] = None,
    oidc_issuer_url: Optional[str] = None,
    oidc_client_id: Optional[str] = None,
    oidc_client_secret: Optional[str] = None,
    oidc_scopes: Optional[str] = None,
    oidc_username_claim: Optional[str] = None,
    oidc_groups_claim: Optional[str] = None,
    oidc_admin_groups: Optional[str] = None,
    oidc_display_name: Optional[str] = None,
    oidc_redirect_uri: Optional[str] = None,
    oidc_allow_insecure_http: Optional[bool] = None,
    commit: bool = True,
    apply_runtime: bool = True,
) -> SystemConfig:
    """Persist config overrides into DB + overlay dict.

    Pass ``None`` for a field to leave it unchanged. Pass an empty string to
    clear the override (fall back to env/default). Pass a value to set.
    """
    config = get_or_create(session, commit=commit)
    pending_overlay: dict[str, object] = {}

    def _apply_str(field_name: str, value: Optional[str]) -> None:
        if value is None:
            return
        db_val = value if value != "" else None
        setattr(config, field_name, db_val)
        effective: object = (
            db_val if db_val is not None else _env_or_default(field_name)
        )
        if field_name in ("data_dir", "thumb_dir") and effective:
            effective = Path(str(effective))
        pending_overlay[field_name] = effective

    def _apply_int(field_name: str, value: Optional[int]) -> None:
        if value is None:
            return
        db_val = value if value != -1 else None
        setattr(config, field_name, db_val)
        if db_val is not None:
            pending_overlay[field_name] = db_val
        else:
            fallback = _env_or_default(field_name)
            try:
                pending_overlay[field_name] = int(fallback or 0)
            except (ValueError, TypeError):
                pending_overlay[field_name] = 30

    def _apply_bool(field_name: str, value: Optional[bool]) -> None:
        if value is None:
            return
        setattr(config, field_name, value)
        pending_overlay[field_name] = value

    _apply_str("storage_backend", storage_backend)
    _apply_str("data_dir", data_dir)
    _apply_str("thumb_dir", thumb_dir)
    _apply_str("s3_bucket", s3_bucket)
    _apply_str("s3_endpoint_url", s3_endpoint_url)
    _apply_str("s3_region", s3_region)
    _apply_str("s3_access_key", s3_access_key)
    _apply_str("s3_secret_key", s3_secret_key)
    _apply_int("backup_retention_days", backup_retention_days)
    _apply_int("trash_retention_days", trash_retention_days)
    _apply_str("backup_s3_bucket", backup_s3_bucket)
    _apply_str("backup_s3_endpoint_url", backup_s3_endpoint_url)
    _apply_str("backup_s3_region", backup_s3_region)
    _apply_str("backup_s3_access_key", backup_s3_access_key)
    _apply_str("backup_s3_secret_key", backup_s3_secret_key)
    _apply_bool("oidc_enabled", oidc_enabled)
    _apply_str("oidc_issuer_url", oidc_issuer_url)
    _apply_str("oidc_client_id", oidc_client_id)
    _apply_str("oidc_client_secret", oidc_client_secret)
    _apply_str("oidc_scopes", oidc_scopes)
    _apply_str("oidc_username_claim", oidc_username_claim)
    _apply_str("oidc_groups_claim", oidc_groups_claim)
    _apply_str("oidc_admin_groups", oidc_admin_groups)
    _apply_str("oidc_display_name", oidc_display_name)
    _apply_str("oidc_redirect_uri", oidc_redirect_uri)
    _apply_bool("oidc_allow_insecure_http", oidc_allow_insecure_http)

    config.updated_at = utcnow()
    session.add(config)
    if commit:
        session.commit()
        session.refresh(config)
    if apply_runtime:
        _overlay.update(pending_overlay)
    if (
        commit
        and apply_runtime
        and any(value is not None for value in (storage_backend, data_dir, thumb_dir))
    ):
        ensure_dirs()

    logger.info("runtime config updated")
    return config


def auto_mark_known_good_enabled(session: Session) -> bool:
    """Whether successful prints should auto-mark their revision known_good."""
    config = session.get(SystemConfig, 1)
    return True if config is None else bool(config.auto_mark_known_good)


def external_libraries_enabled(session: Session) -> bool:
    """Master opt-in switch for NAS folder mirroring. Off by default."""
    config = session.get(SystemConfig, 1)
    return False if config is None else bool(config.external_libraries_enabled)


def notifications_enabled(session: Session) -> bool:
    """Master opt-in switch for outbound notifications. Off by default."""
    config = session.get(SystemConfig, 1)
    return False if config is None else bool(config.notifications_enabled)


def set_notifications_enabled(session: Session, enabled: bool) -> SystemConfig:
    config = get_or_create(session)
    config.notifications_enabled = enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def spoolman_enabled(session: Session) -> bool:
    """Master opt-in switch for the Spoolman integration. Off by default."""
    config = session.get(SystemConfig, 1)
    return False if config is None else bool(config.spoolman_enabled)


def set_spoolman_enabled(session: Session, enabled: bool) -> SystemConfig:
    config = get_or_create(session)
    config.spoolman_enabled = enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def spoolman_write_enabled(session: Session) -> bool:
    """Whether measured consumption is written back to Spoolman. Off by default;
    the write path additionally skips the decrement at runtime when Moonraker's
    native hook is already counting the active spool (unless write-force is set —
    see ``spoolman_write_force``)."""
    config = session.get(SystemConfig, 1)
    return False if config is None else bool(config.spoolman_write_enabled)


def set_spoolman_write_enabled(session: Session, enabled: bool) -> SystemConfig:
    config = get_or_create(session)
    config.spoolman_write_enabled = enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def spoolman_write_force(session: Session) -> bool:
    """Whether to write consumption back even when Spoolman reports an active
    spool (Moonraker's native hook). Off by default so the double-count guard
    holds for users who never open the Spoolman settings card."""
    config = session.get(SystemConfig, 1)
    return False if config is None else bool(config.spoolman_write_force)


def set_spoolman_write_force(session: Session, force: bool) -> SystemConfig:
    config = get_or_create(session)
    config.spoolman_write_force = force
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def spoolman_config(session: Session) -> dict:
    """Internal connection config (includes the real API key for client use)."""
    config = session.get(SystemConfig, 1)
    if config is None:
        return {"base_url": None, "api_key": None}
    return {
        "base_url": config.spoolman_base_url,
        "api_key": config.spoolman_api_key,
    }


def set_spoolman_config(
    session: Session,
    *,
    base_url: Any = _UNSET,
    api_key: Any = _UNSET,
) -> SystemConfig:
    """Persist base URL and/or API key. ``_UNSET`` leaves a field untouched so a
    blank/masked key from the UI never clobbers a stored secret."""
    config = get_or_create(session)
    if base_url is not _UNSET:
        config.spoolman_base_url = (base_url or "").strip().rstrip("/") or None
    if api_key is not _UNSET:
        config.spoolman_api_key = api_key or None
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def currency(session: Session) -> str:
    """ISO 4217 code used to render cost figures. Defaults to USD."""
    config = session.get(SystemConfig, 1)
    return (config.currency if config and config.currency else None) or "USD"


def set_currency(session: Session, code: str) -> SystemConfig:
    config = get_or_create(session)
    config.currency = code.upper() if code else None
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def set_external_libraries_enabled(session: Session, enabled: bool) -> SystemConfig:
    config = get_or_create(session)
    config.external_libraries_enabled = enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def set_auto_mark_known_good(session: Session, enabled: bool) -> SystemConfig:
    config = get_or_create(session)
    config.auto_mark_known_good = enabled
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def set_makerworld_token(session: Session, token: str) -> SystemConfig:
    """Persist a MakerWorld session token and apply it as the import cookie now."""
    config = get_or_create(session)
    config.makerworld_token = token or None
    config.makerworld_token_updated_at = utcnow() if token else None
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    if token:
        _overlay["makerworld_cookie"] = f"token={token}"
    else:
        _overlay.pop("makerworld_cookie", None)
    logger.info("MakerWorld token %s", "set" if token else "cleared")
    return config


def clear_makerworld_token(session: Session) -> SystemConfig:
    """Disconnect MakerWorld: drop the stored token and the overlay cookie."""
    return set_makerworld_token(session, "")


def makerworld_status(session: Session) -> dict:
    """Connection status for the MakerWorld login UI (never returns the token)."""
    config = session.get(SystemConfig, 1)
    connected = bool(config and config.makerworld_token)
    updated_at = config.makerworld_token_updated_at if config else None
    return {
        "connected": connected,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def mark_configured(session: Session, *, commit: bool = True) -> SystemConfig:
    config = get_or_create(session, commit=commit)
    if config.configured_at is None:
        config.configured_at = utcnow()
    config.updated_at = utcnow()
    session.add(config)
    if commit:
        session.commit()
        session.refresh(config)
    return config


def get_effective_config(session: Session) -> dict:
    """Return the current effective config (env + DB overlay merged).

    ``settings`` is now a ConfigResolver — attribute reads already resolve
    overlay-preferred values. No manual merge needed.
    """
    return {
        "storage_backend": str(settings.storage_backend),
        "data_dir": str(settings.data_dir),
        "thumb_dir": str(settings.thumb_dir),
        "s3_bucket": str(settings.s3_bucket),
        "s3_endpoint_url": str(settings.s3_endpoint_url),
        "s3_region": str(settings.s3_region),
        "s3_access_key": _mask_secret(str(settings.s3_access_key)),
        "s3_secret_key": _mask_secret(str(settings.s3_secret_key)),
        "has_s3_access_key": bool(settings.s3_access_key),
        "has_s3_secret_key": bool(settings.s3_secret_key),
        "backup_retention_days": int(settings.backup_retention_days),
        "trash_retention_days": int(settings.trash_retention_days),
        "backup_s3_bucket": str(settings.backup_s3_bucket),
        "backup_s3_endpoint_url": str(settings.backup_s3_endpoint_url),
        "backup_s3_region": str(settings.backup_s3_region),
        "backup_s3_access_key": _mask_secret(str(settings.backup_s3_access_key)),
        "backup_s3_secret_key": _mask_secret(str(settings.backup_s3_secret_key)),
        "has_backup_s3_access_key": bool(settings.backup_s3_access_key),
        "has_backup_s3_secret_key": bool(settings.backup_s3_secret_key),
        "has_backup_s3": bool(settings.backup_s3_bucket),
        "auto_mark_known_good": auto_mark_known_good_enabled(session),
        "external_libraries_enabled": external_libraries_enabled(session),
        "notifications_enabled": notifications_enabled(session),
        "spoolman_enabled": spoolman_enabled(session),
        "currency": currency(session),
        "oidc_enabled": bool(settings.oidc_enabled),
        "oidc_issuer_url": str(settings.oidc_issuer_url),
        "oidc_client_id": str(settings.oidc_client_id),
        "has_oidc_client_secret": bool(settings.oidc_client_secret),
        "oidc_scopes": str(settings.oidc_scopes),
        "oidc_username_claim": str(settings.oidc_username_claim),
        "oidc_groups_claim": str(settings.oidc_groups_claim),
        "oidc_admin_groups": str(settings.oidc_admin_groups),
        "oidc_display_name": str(settings.oidc_display_name),
        "oidc_redirect_uri": str(settings.oidc_redirect_uri),
        "oidc_allow_insecure_http": bool(settings.oidc_allow_insecure_http),
    }


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]
