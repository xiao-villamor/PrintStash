"""Runtime overlay for all configurable settings.

Writes overrides to the shared ``_overlay`` dict (defined in ``app.core.config``)
instead of mutating the ``settings`` singleton. The ``ConfigResolver`` reads
``_overlay`` on every attribute access, so all 16+ call sites see the effective
value without code changes. See ADR-0002.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.core.config import DEFAULT_JWT_SECRET, _overlay, ensure_dirs, settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import SystemConfig, User
from app.services.storage_providers import (
    SFTPProviderConfig,
    StorageProviderConfig,
    merge_provider_secrets,
    parse_provider_config,
    resolve_transport,
    sanitized_provider_config,
    split_provider_config,
)

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

    if config.storage_provider and config.storage_provider_config_json:
        provider = _stored_provider_config(config)
        if provider is not None:
            _project_provider_overlay(provider)
    if config.data_dir and not config.storage_provider:
        _overlay["data_dir"] = Path(config.data_dir)
    if config.thumb_dir and not config.storage_provider:
        _overlay["thumb_dir"] = Path(config.thumb_dir)
    if not config.storage_provider:
        if config.storage_backend:
            # An explicit legacy DB backend outranks the new provider env input.
            # The empty overlay value intentionally shadows the frozen env value.
            _overlay["storage_provider"] = ""
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
    if config.model_thumbnail_width is not None:
        _overlay["model_thumbnail_width"] = config.model_thumbnail_width
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


def apply_overlay(session: Session) -> None:
    """Replace runtime overrides with values persisted in ``system_config``."""
    config = session.get(SystemConfig, 1)
    if config is None:
        return
    _overlay.clear()
    _merge_config_overlay(config)
    apply_environment_storage_provider(session)


def apply_environment_storage_provider(session: Session) -> None:
    """Project new provider env input only when no DB storage source exists."""
    config = session.get(SystemConfig, 1)
    has_db_storage = config is not None and bool(
        config.storage_provider or config.storage_backend
    )
    if not has_db_storage and settings.storage_provider:
        try:
            nonsecret = _json_object(str(settings.storage_provider_config))
            secrets_map = {
                str(k): str(v)
                for k, v in _json_object(str(settings.storage_provider_secrets)).items()
                if v
            }
            parsed = merge_provider_secrets(nonsecret, secrets_map)
            if parsed.provider != settings.storage_provider:
                raise ValueError("storage_provider_config_mismatch")
            _project_provider_overlay(parsed)
        except ValueError:
            logger.exception("environment provider configuration is invalid")


def activate_config(config: SystemConfig) -> None:
    """Merge a newly committed config into live runtime state."""
    _merge_config_overlay(config)
    ensure_dirs()


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stored_provider_config(config: SystemConfig) -> StorageProviderConfig | None:
    if not config.storage_provider or not config.storage_provider_config_json:
        return None
    nonsecret = _json_object(config.storage_provider_config_json)
    secrets_json = _json_object(config.storage_provider_secret_json)
    secrets_map = {str(k): str(v) for k, v in secrets_json.items() if v}
    try:
        return merge_provider_secrets(nonsecret, secrets_map)
    except ValueError:
        logger.exception("stored provider configuration is invalid")
        return None


def _project_provider_overlay(config: StorageProviderConfig) -> None:
    spec = resolve_transport(config)
    _overlay["storage_provider"] = config.provider
    _overlay["storage_provider_config"] = config.model_dump_json()
    if spec.kind.value == "local":
        _overlay["storage_backend"] = "local"
        _overlay["data_dir"] = Path(str(spec.options["data_dir"]))
        _overlay["thumb_dir"] = Path(str(spec.options["thumb_dir"]))
    elif spec.kind.value == "s3":
        _overlay["storage_backend"] = "s3"
        _overlay["s3_bucket"] = str(spec.options["bucket"])
        _overlay["s3_endpoint_url"] = str(spec.options["endpoint_url"])
        _overlay["s3_region"] = str(spec.options["region"])
        _overlay["s3_access_key"] = str(spec.options["access_key"])
        _overlay["s3_secret_key"] = str(spec.options["secret_key"])
    else:
        _overlay["storage_backend"] = config.provider


def update_storage_provider(
    session: Session,
    *,
    provider: str,
    raw_config: dict[str, Any],
    commit: bool = True,
    apply_runtime: bool = True,
) -> SystemConfig:
    """Validate, persist, and sanitize one discriminated provider config."""
    config = get_or_create(session, commit=commit)
    if raw_config.get("provider") != provider:
        raise ValueError("storage_provider_config_mismatch")
    parsed = resolve_requested_storage_provider(
        config, provider=provider, raw_config=raw_config
    )
    nonsecret, secrets_map = split_provider_config(parsed)
    config.storage_provider = provider
    config.storage_provider_config_json = json.dumps(nonsecret, separators=(",", ":"))
    config.storage_provider_secret_json = json.dumps(secrets_map, separators=(",", ":"))
    # Compatibility projections remain available to older releases/readers.
    spec = resolve_transport(parsed)
    config.storage_backend = (
        "s3"
        if spec.kind.value == "s3"
        else "local"
        if spec.kind.value == "local"
        else provider
    )
    if spec.kind.value == "local":
        config.data_dir = str(spec.options["data_dir"])
        config.thumb_dir = str(spec.options["thumb_dir"])
    elif spec.kind.value == "s3":
        config.s3_bucket = str(spec.options["bucket"])
        config.s3_endpoint_url = str(spec.options["endpoint_url"])
        config.s3_region = str(spec.options["region"])
        config.s3_access_key = str(spec.options["access_key"])
        config.s3_secret_key = str(spec.options["secret_key"])
    config.updated_at = utcnow()
    session.add(config)
    if commit:
        session.commit()
        session.refresh(config)
    if apply_runtime:
        _project_provider_overlay(parsed)
    return config


def resolve_requested_storage_provider(
    config: SystemConfig,
    *,
    provider: str,
    raw_config: dict[str, Any],
) -> StorageProviderConfig:
    prior_config = _json_object(config.storage_provider_config_json)
    prior_secrets = _json_object(config.storage_provider_secret_json)
    if config.storage_provider == provider:
        merged = {**prior_config, **prior_secrets, **raw_config}
        if provider == "sftp":
            if raw_config.get("password"):
                merged.pop("private_key_path", None)
                merged.pop("passphrase", None)
            elif raw_config.get("private_key_path"):
                merged.pop("password", None)
    else:
        merged = raw_config
    return parse_provider_config(merged)


def storage_provider_signature(config: StorageProviderConfig) -> tuple[object, ...]:
    # Host-key and authentication rotation must not look like a namespace
    # migration. Legacy SFTP rows may lack a host key, so build this identity
    # without crossing the stricter activation boundary in ``resolve_transport``.
    if isinstance(config, SFTPProviderConfig):
        return (
            "sftp",
            config.provider,
            f"sftp/{config.root}",
            config.host,
            config.port,
            config.username,
        )
    spec = resolve_transport(config)
    namespace_options = {
        "bucket",
        "data_dir",
        "endpoint_url",
        "host",
        "path_style",
        "port",
        "region",
        "root",
        "thumb_dir",
        "username",
    }
    return (
        spec.kind.value,
        spec.provider,
        spec.namespace,
        tuple(
            sorted(
                (key, str(value))
                for key, value in spec.options.items()
                if key in namespace_options
            )
        ),
    )


def storage_namespace_change_requires_migration(
    session: Session,
    *,
    storage_backend: str | None = None,
    data_dir: str | None = None,
    thumb_dir: str | None = None,
    s3_bucket: str | None = None,
    s3_endpoint_url: str | None = None,
    s3_region: str | None = None,
    storage_provider: str | None = None,
    storage_provider_config: dict[str, Any] | None = None,
) -> tuple[bool, StorageProviderConfig | None]:
    """Own provider projection and migration gating for config routes."""
    config = get_config(session)

    def changed(value: str | None, current: object, *, path: bool = False) -> bool:
        if value is None:
            return False
        if value == "":
            return str(current) != ""
        if path:
            return Path(value).expanduser().resolve(strict=False) != Path(
                str(current)
            ).expanduser().resolve(strict=False)
        return value != str(current)

    changed_legacy = any(
        (
            changed(storage_backend, settings.storage_backend),
            changed(data_dir, settings.data_dir, path=True),
            changed(thumb_dir, settings.thumb_dir, path=True),
            changed(s3_bucket, settings.s3_bucket),
            changed(s3_endpoint_url, settings.s3_endpoint_url),
            changed(s3_region, settings.s3_region),
        )
    )
    requested = None
    if storage_provider is not None and storage_provider_config is not None:
        requested = resolve_requested_storage_provider(
            config, provider=storage_provider, raw_config=storage_provider_config
        )
        current = _stored_provider_config(config)
        changed_legacy = current is None or (
            storage_provider_signature(current) != storage_provider_signature(requested)
        )
        # This is also the validation boundary for API updates. In particular,
        # old SFTP rows remain readable, while saving/activating one without a
        # pinned host key returns the actionable validation error.
        resolve_transport(requested)
    return changed_legacy, requested


def has_storage_state(session: Session) -> bool:
    """Whether changing the namespace would orphan existing domain rows."""
    from app.db.models import Collection, Document, File, Model

    return any(
        session.exec(select(table.id).limit(1)).first() is not None
        for table in (File, Document, Model, Collection)
    )


def get_sanitized_storage_provider(
    session: Session,
) -> tuple[str, dict[str, object]] | None:
    config = session.get(SystemConfig, 1)
    if config is None or not config.storage_provider:
        return None
    nonsecret = _json_object(config.storage_provider_config_json)
    secrets_map = {
        str(k): str(v)
        for k, v in _json_object(config.storage_provider_secret_json).items()
        if v
    }
    return config.storage_provider, sanitized_provider_config(nonsecret, secrets_map)


def ensure_jwt_secret(session: Session) -> None:
    """Guarantee this install does not sign tokens with the published default.

    Env wins and is never copied into the DB. Otherwise the persisted secret is
    applied, or a fresh one is generated and stored so it survives restarts —
    regenerating on every boot would log everyone out on every restart.

    Refusing to boot instead would brick every existing install, since the
    compose files default ``VAULT_JWT_SECRET`` to the shipped value.
    """
    configured_secret = settings.jwt_secret.strip()
    if configured_secret and configured_secret != DEFAULT_JWT_SECRET:
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
    model_thumbnail_width: Optional[int] = None,
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
    _apply_int("model_thumbnail_width", model_thumbnail_width)
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
    """Compatibility helper used only to clear a token from older installs."""
    del token
    config = get_or_create(session)
    config.makerworld_token = None
    config.makerworld_token_updated_at = None
    config.updated_at = utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    _overlay.pop("makerworld_cookie", None)
    logger.info("legacy MakerWorld token cleared")
    return config


def clear_makerworld_token(session: Session) -> SystemConfig:
    """Disconnect MakerWorld: drop the stored token and the overlay cookie."""
    return set_makerworld_token(session, "")


def makerworld_status(session: Session) -> dict:
    """Compatibility status: MakerWorld connections are no longer server-side."""
    del session
    return {"connected": False, "updated_at": None}


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
        "model_thumbnail_width": int(settings.model_thumbnail_width),
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
