"""First-run setup wizard.

While the install is unconfigured (no ``system_config.configured_at`` and no
users), this router is the *only* write surface that accepts traffic without
auth. Once ``POST /setup`` succeeds, the endpoint becomes read-only and
returns 409 on further attempts — re-running the wizard would let an attacker
seize an established vault.

Storage path validation is deliberately fail-safe: local vault directories
must be writable and empty on first setup.  An existing model library belongs
behind the external-library indexing workflow, never the private blob-store
path.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.core.config import FrozenSettings, ensure_dirs, settings
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_session
from app.schemas.setup import SetupRequest, SetupResponse, SetupStatus
from app.services import runtime_config
from app.services.auth import create_access_token, hash_password, set_session_cookie
from app.services.setup_token import verify_setup_token
from app.services.storage_paths import (
    StoragePathOverlapError,
    sqlite_database_path,
    validate_disjoint_directories,
    validate_file_outside_roots,
)
from app.services.storage_providers import TransportKind, resolve_transport

logger = get_logger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])
_setup_lock = threading.Lock()


# Pydantic Settings exposes its defaults via ``model_fields``. We pull the
# *original* env-time defaults so the wizard can show the user what they'd
# get if they left the field blank, even after a later edit mutates them.
_DEFAULT_DATA_DIR = str(FrozenSettings.model_fields["data_dir"].default)
_DEFAULT_THUMB_DIR = str(FrozenSettings.model_fields["thumb_dir"].default)


def _validate_writable_dir(
    path_str: str, label: str, *, require_empty: bool = False
) -> Path:
    """Create a directory and confirm it is writable and safe for first use."""
    try:
        path = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid_{label}_path",
        ) from exc

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("setup: cannot create %s=%s: %s", label, path, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label}_not_creatable",
        ) from exc

    if require_empty:
        try:
            populated = next(path.iterdir(), None) is not None
        except OSError as exc:
            logger.warning("setup: cannot inspect %s=%s: %s", label, path, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label}_not_readable",
            ) from exc
        if populated:
            logger.warning("setup: refusing populated private vault %s=%s", label, path)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{label}_not_empty",
            )

    probe: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path,
            prefix=".printstash-write-probe-",
            delete=False,
        ) as handle:
            handle.write("ok")
            probe = Path(handle.name)
    except OSError as exc:
        logger.warning("setup: %s=%s not writable: %s", label, path, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label}_not_writable",
        ) from exc
    finally:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    return path


@router.get("/status", response_model=SetupStatus, response_model_exclude_none=True)
def get_status(session: Session = Depends(get_session)) -> SetupStatus:
    """Lightweight probe — safe to call on every page load."""
    config = runtime_config.get_config(session)
    user_count = len(session.exec(select(User.id)).all())
    configured = config.configured_at is not None and user_count > 0
    if configured:
        return SetupStatus(configured=True)
    provider_config = runtime_config.get_sanitized_storage_provider(session)
    return SetupStatus(
        configured=configured,
        setup_token_required=True,
        user_count=user_count,
        default_data_dir=_DEFAULT_DATA_DIR,
        default_thumb_dir=_DEFAULT_THUMB_DIR,
        current_data_dir=str(settings.data_dir),
        current_thumb_dir=str(settings.thumb_dir),
        current_storage_backend=str(settings.storage_backend),
        current_storage_provider=(provider_config[0] if provider_config else None),
        current_storage_provider_config=(
            provider_config[1] if provider_config else None
        ),
        current_s3_bucket=str(settings.s3_bucket),
        current_s3_endpoint_url=str(settings.s3_endpoint_url),
        current_s3_region=str(settings.s3_region),
        current_backup_retention_days=int(settings.backup_retention_days),
        current_backup_s3_bucket=str(settings.backup_s3_bucket),
        current_backup_s3_endpoint_url=str(settings.backup_s3_endpoint_url),
        current_backup_s3_region=str(settings.backup_s3_region),
        configured_at=config.configured_at,
    )


@router.post("", response_model=SetupResponse, status_code=status.HTTP_201_CREATED)
def complete_setup(
    body: SetupRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> SetupResponse:
    with _setup_lock:
        try:
            result = _complete_setup(body, session)
        except Exception:
            session.rollback()
            raise
        set_session_cookie(response, result.access_token)
        return result


def _complete_setup(body: SetupRequest, session: Session) -> SetupResponse:
    """Create the first superuser and (optionally) persist storage paths.

    Refuses to run if the vault is already configured. The wizard hands back a
    JWT so the browser can flow straight into the authenticated app without a
    second round-trip through ``/auth/login``.
    """
    if runtime_config.is_configured(session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already_configured",
        )

    if not verify_setup_token(body.setup_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_setup_token",
        )

    # Guard against a half-configured state (e.g. user table seeded out of band).
    existing = session.exec(select(User).limit(1)).first()
    if existing is not None:
        # An admin already exists from a legacy ensure_default_user run.
        # Block silent first-user creation; operator must use that account.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="users_already_exist",
        )

    legacy_storage_fields = {
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "s3_bucket",
        "s3_endpoint_url",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
    }
    new_storage_supplied = (
        body.storage_provider is not None or body.storage_provider_config is not None
    )
    if new_storage_supplied and body.model_fields_set & legacy_storage_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="mixed_storage_provider_input",
        )
    if (body.storage_provider is None) != (body.storage_provider_config is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="storage_provider_and_config_required",
        )
    requested_provider = None
    transport = None
    if body.storage_provider is not None and body.storage_provider_config is not None:
        try:
            requested_provider = runtime_config.resolve_requested_storage_provider(
                runtime_config.get_config(session),
                provider=body.storage_provider,
                raw_config=body.storage_provider_config,
            )
            transport = resolve_transport(requested_provider)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
    storage_backend = (
        "local"
        if transport is not None and transport.kind is TransportKind.LOCAL
        else "s3"
        if transport is not None and transport.kind is TransportKind.S3
        else requested_provider.provider
        if requested_provider is not None
        else body.storage_backend or str(settings.storage_backend)
    )
    if requested_provider is None and storage_backend not in ("local", "s3"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_storage_backend",
        )
    if storage_backend == "s3" and not (
        str(transport.options["bucket"])
        if transport is not None and transport.kind is TransportKind.S3
        else (body.s3_bucket or "").strip() or str(settings.s3_bucket)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="s3_bucket_required",
        )

    # 1. Validate local storage paths first — fail fast before mutating anything.
    if storage_backend == "local":
        # The browser omits unchanged defaults, so validate the effective paths,
        # not only explicit overrides.  This catches a populated or read-only
        # bind mount at /data/files before setup mutates any database state.
        effective_data_dir = (
            str(transport.options["data_dir"])
            if transport is not None
            else body.data_dir or str(settings.data_dir)
        )
        effective_thumb_dir = (
            str(transport.options["thumb_dir"])
            if transport is not None
            else body.thumb_dir or str(settings.thumb_dir)
        )
        protected_dirs: dict[str, str | Path] = {
            "data_dir": effective_data_dir,
            "thumb_dir": effective_thumb_dir,
            "staging_dir": settings.staging_dir,
            "backup_dir": settings.backup_dir,
        }
        try:
            resolved = validate_disjoint_directories(protected_dirs)
            database_path = sqlite_database_path(str(settings.db_url))
            if database_path is not None:
                validate_file_outside_roots(database_path, resolved)
            validate_file_outside_roots(settings.secrets_key_file, resolved)
        except (OSError, RuntimeError, StoragePathOverlapError) as exc:
            logger.warning("setup: refusing overlapping storage paths: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="storage_paths_overlap",
            ) from exc

        effective_data_dir = str(resolved["data_dir"])
        effective_thumb_dir = str(resolved["thumb_dir"])
        _validate_writable_dir(
            effective_data_dir,
            "data_dir",
            require_empty=True,
        )
        _validate_writable_dir(
            effective_thumb_dir,
            "thumb_dir",
            require_empty=True,
        )
    else:
        effective_data_dir = body.data_dir
        effective_thumb_dir = body.thumb_dir

    effective_s3_bucket = (
        str(transport.options["bucket"])
        if transport is not None and transport.kind is TransportKind.S3
        else body.s3_bucket
        if body.s3_bucket is not None
        else str(settings.s3_bucket)
    )
    effective_s3_endpoint_url = (
        str(transport.options["endpoint_url"])
        if transport is not None and transport.kind is TransportKind.S3
        else body.s3_endpoint_url
        if body.s3_endpoint_url is not None
        else str(settings.s3_endpoint_url)
    )
    effective_s3_region = (
        str(transport.options["region"])
        if transport is not None and transport.kind is TransportKind.S3
        else body.s3_region
        if body.s3_region is not None
        else str(settings.s3_region)
    )

    # 2. Persist storage and backup overrides into the runtime overlay.
    if requested_provider is not None:
        runtime_config.update_storage_provider(
            session,
            provider=body.storage_provider or "",
            raw_config=body.storage_provider_config or {},
            commit=False,
            apply_runtime=False,
        )
    runtime_config.update_config(
        session,
        storage_backend=None if requested_provider is not None else storage_backend,
        # Pin the effective roots. Leaving these null would let a later env
        # change silently reinterpret existing rows against a different mount.
        data_dir=None if requested_provider is not None else effective_data_dir,
        thumb_dir=None if requested_provider is not None else effective_thumb_dir,
        # Pin the remote namespace identity for the same reason as local roots:
        # environment drift must not reinterpret owned keys in another bucket.
        s3_bucket=None if requested_provider is not None else effective_s3_bucket,
        s3_endpoint_url=None
        if requested_provider is not None
        else effective_s3_endpoint_url,
        s3_region=None if requested_provider is not None else effective_s3_region,
        s3_access_key=None if requested_provider is not None else body.s3_access_key,
        s3_secret_key=None if requested_provider is not None else body.s3_secret_key,
        backup_retention_days=body.backup_retention_days,
        backup_s3_bucket=body.backup_s3_bucket,
        backup_s3_endpoint_url=body.backup_s3_endpoint_url,
        backup_s3_region=body.backup_s3_region,
        backup_s3_access_key=body.backup_s3_access_key,
        backup_s3_secret_key=body.backup_s3_secret_key,
        commit=False,
        apply_runtime=False,
    )

    # 3. Create the superuser.
    user = User(
        username=body.username.strip(),
        email=(body.email.strip() if body.email else None) or None,
        hashed_password=hash_password(body.password),
        is_superuser=True,
        is_active=True,
    )
    session.add(user)

    # 4. Stamp the config as completed.
    config = runtime_config.mark_configured(session, commit=False)
    session.commit()
    session.refresh(user)
    # Runtime state changes only after the DB transaction has committed.
    runtime_config.activate_config(config)
    # A fresh setup owns the empty roots it just created.  Enroll them with
    # the persisted installation identity so the next startup can distinguish
    # this mount from an accidental empty shadow directory.
    if settings.storage_backend == "local":
        # First-run setup is the sole flow allowed to provision managed roots.
        ensure_dirs(create_managed_roots=True)
        from app.services.storage_backend import enroll_legacy_local_root

        identity = runtime_config.ensure_storage_identity(session)
        for role, root in (
            ("data", Path(settings.data_dir)),
            ("thumb", Path(settings.thumb_dir)),
        ):
            if not enroll_legacy_local_root(
                root,
                role=role,
                installation=identity,
                proofs=[],
                allow_empty=True,
            ):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="storage_root_enrollment_failed",
                )

    logger.info(
        "first-run setup complete: user=%s data_dir=%s thumb_dir=%s",
        user.username,
        settings.data_dir,
        settings.thumb_dir,
    )

    token = create_access_token(
        user.id, user.username, scope="admin", auth_version=user.auth_version
    )
    return SetupResponse(
        configured=True,
        user_id=user.id,
        username=user.username,
        storage_backend=str(settings.storage_backend),
        storage_provider=(body.storage_provider or str(settings.storage_backend)),
        data_dir=str(settings.data_dir),
        thumb_dir=str(settings.thumb_dir),
        access_token=token,
    )
