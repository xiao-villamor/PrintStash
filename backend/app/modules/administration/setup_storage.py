"""Storage preparation for the first owner of an installation.

Storage path validation is deliberately fail-safe: local vault directories must
be writable and empty on first setup. An existing model library belongs behind
the external-library indexing workflow, never the private blob-store path.

Two flows reach this module. Browser registration chooses storage in the same
request that creates the owner. An owner provisioned from the environment signs
in first and chooses afterwards through :func:`choose`.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from app.core.config import ensure_dirs, settings
from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.db.models import SystemConfig
from app.modules.administration import runtime_config
from app.modules.storage.storage_paths import (
    StoragePathOverlapError,
    sqlite_database_path,
    validate_disjoint_directories,
    validate_file_outside_roots,
)
from app.modules.storage.storage_providers import (
    StorageProviderConfig,
    TransportKind,
    resolve_transport,
)
from app.schemas.setup import SetupStorageCheck, SetupStorageRequest

logger = get_logger(__name__)


@dataclass(frozen=True)
class PreparedStorage:
    requested_provider: StorageProviderConfig | None
    storage_backend: str
    data_dir: str | None
    thumb_dir: str | None
    s3_bucket: str | None
    s3_endpoint_url: str | None
    s3_region: str | None


def _validate_writable_dir(
    path_str: str, label: str, *, require_empty: bool = False
) -> Path:
    """Create a directory and confirm it is writable and safe for first use."""
    try:
        path = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise OperationError(f"invalid_{label}_path") from exc

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("setup: cannot create %s=%s: %s", label, path, exc)
        raise OperationError(f"{label}_not_creatable") from exc

    if require_empty:
        try:
            populated = next(path.iterdir(), None) is not None
        except OSError as exc:
            logger.warning("setup: cannot inspect %s=%s: %s", label, path, exc)
            raise OperationError(f"{label}_not_readable") from exc
        if populated:
            logger.warning("setup: refusing populated private vault %s=%s", label, path)
            raise OperationError(f"{label}_not_empty")

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
        raise OperationError(f"{label}_not_writable") from exc
    finally:
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    return path


def prepare(
    body: SetupStorageRequest, session: Session, *, provision: bool
) -> PreparedStorage:
    """Validate a storage choice and resolve its effective roots.

    Nothing durable is written here, so a refused choice leaves setup exactly
    as it was.
    """
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
        raise OperationError(
            "mixed_storage_provider_input", kind=ErrorKind.UNPROCESSABLE
        )
    if (body.storage_provider is None) != (body.storage_provider_config is None):
        raise OperationError(
            "storage_provider_and_config_required", kind=ErrorKind.UNPROCESSABLE
        )
    requested_provider = None
    transport = None
    if body.storage_provider is not None and body.storage_provider_config is not None:
        try:
            requested_provider = runtime_config.resolve_requested_storage_provider(
                # Validate and provision the remote root before creating the
                # singleton config row. A failed first-run provision must not
                # leave durable setup state behind.
                session.get(SystemConfig, 1) or SystemConfig(),
                provider=body.storage_provider,
                raw_config=body.storage_provider_config,
            )
            transport = resolve_transport(requested_provider)
        except ValueError as exc:
            raise OperationError(str(exc), kind=ErrorKind.UNPROCESSABLE) from exc

        # A remote SFTP root may be absent on a new NAS share.  Provision it
        # only inside an owner-authorized first-run flow; normal startup and
        # health checks remain read-only and fail closed when an enrolled root
        # disappears.
        if provision and transport is not None and transport.kind is TransportKind.SFTP:
            try:
                from app.modules.storage.storage_opendal import OpenDALStorageBackend

                OpenDALStorageBackend(transport).provision_root()
            except Exception as exc:
                logger.warning("setup: unable to provision SFTP root", exc_info=True)
                raise OperationError("sftp_root_not_provisionable") from exc
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
        raise OperationError("invalid_storage_backend")
    if storage_backend == "s3" and not (
        str(transport.options["bucket"])
        if transport is not None and transport.kind is TransportKind.S3
        else (body.s3_bucket or "").strip() or str(settings.s3_bucket)
    ):
        raise OperationError("s3_bucket_required")

    # Validate local storage paths first — fail fast before mutating anything.
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
            raise OperationError("storage_paths_overlap") from exc

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

    return PreparedStorage(
        requested_provider,
        storage_backend,
        effective_data_dir,
        effective_thumb_dir,
        effective_s3_bucket,
        effective_s3_endpoint_url,
        effective_s3_region,
    )


def check(
    body: SetupStorageRequest, prepared: PreparedStorage
) -> list[SetupStorageCheck]:
    """Prove the prepared storage can be written before anything depends on it."""
    checks = [SetupStorageCheck(code="configuration_valid")]
    if prepared.storage_backend == "local":
        for label, path in (
            ("data", prepared.data_dir),
            ("thumbnails", prepared.thumb_dir),
        ):
            try:
                free = shutil.disk_usage(path).free if path is not None else None
            except OSError:
                free = None
            checks.append(SetupStorageCheck(code=f"{label}_writable", free_bytes=free))
        return checks
    try:
        backend = remote_backend(prepared.requested_provider, body)
        backend.ensure_setup()
        if not backend.capabilities.conditional_create:
            raise ValueError("remote_write_unavailable")
    except Exception as exc:
        raise OperationError("setup_remote_storage_unavailable") from exc
    checks.append(SetupStorageCheck(code="remote_read_write_verified"))
    return checks


def persist_choice(
    session: Session, body: SetupStorageRequest, prepared: PreparedStorage
) -> None:
    """Stage the storage and backup choice; the caller owns the commit."""
    requested_provider = prepared.requested_provider
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
        storage_backend=None
        if requested_provider is not None
        else prepared.storage_backend,
        # Pin the effective roots. Leaving these null would let a later env
        # change silently reinterpret existing rows against a different mount.
        data_dir=None if requested_provider is not None else prepared.data_dir,
        thumb_dir=None if requested_provider is not None else prepared.thumb_dir,
        # Pin the remote namespace identity for the same reason as local roots:
        # environment drift must not reinterpret owned keys in another bucket.
        s3_bucket=None if requested_provider is not None else prepared.s3_bucket,
        s3_endpoint_url=None
        if requested_provider is not None
        else prepared.s3_endpoint_url,
        s3_region=None if requested_provider is not None else prepared.s3_region,
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


def choice_required(config: SystemConfig | None) -> bool:
    """True while an owner exists but nobody has chosen where files live.

    Browser registration persists storage with the owner, so only an owner
    provisioned from the environment reaches this state. A choice that was
    made but not yet activated is a retry, not a new choice.
    """
    return bool(
        config is not None
        and config.configured_at is not None
        and config.setup_storage_pending
        # The same test ``apply_environment_storage_provider`` uses for a
        # persisted storage source.
        and not (config.storage_provider or config.storage_backend)
    )


def choose(session: Session, body: SetupStorageRequest) -> None:
    """Persist and activate the storage the signed-in owner chose.

    The choice is checked before it is persisted, so a mistyped remote setting
    is refused without stranding the installation on it.
    """
    config = runtime_config.get_config(session)
    if not choice_required(config):
        raise OperationError("setup_storage_already_chosen", kind=ErrorKind.CONFLICT)
    prepared = prepare(body, session, provision=True)
    check(body, prepared)
    persist_choice(session, body, prepared)
    session.commit()
    finish(session, config)
    logger.info(
        "first-run storage chosen: backend=%s data_dir=%s thumb_dir=%s",
        settings.storage_backend,
        settings.data_dir,
        settings.thumb_dir,
    )


def prepare_pending(session: Session, body: SetupStorageRequest | None) -> None:
    """Finish storage for a signed-in owner, choosing it first when nobody has.

    Without a choice this retries an activation that failed after the choice was
    persisted. An owner provisioned from ``VAULT_SETUP_ADMIN_*`` has nothing to
    retry, so finishing would activate unpinned environment defaults nobody chose;
    that is refused rather than guessed. An empty body counts as no choice.
    """
    config = runtime_config.get_config(session)
    if config.configured_at is None:
        raise OperationError("setup_not_completed", kind=ErrorKind.CONFLICT)
    if body is not None and body.model_fields_set:
        choose(session, body)
    elif choice_required(config):
        raise OperationError("setup_storage_choice_required", kind=ErrorKind.CONFLICT)
    elif config.setup_storage_pending:
        finish(session, config)


def _enrollment_failed() -> OperationError:
    # Retryable: the account and the choice are kept, and the owner retries.
    return OperationError("storage_root_enrollment_failed", kind=ErrorKind.UNAVAILABLE)


def remote_backend(
    provider: StorageProviderConfig | None, legacy: SetupStorageRequest | None = None
):
    from app.modules.storage.storage_backend.s3 import S3StorageBackend
    from app.modules.storage.storage_opendal import OpenDALStorageBackend

    if provider is None and legacy is not None:
        from app.modules.storage.storage_providers import S3ProviderConfig

        provider = S3ProviderConfig(
            provider="s3",
            bucket=legacy.s3_bucket or settings.s3_bucket,
            endpoint_url=legacy.s3_endpoint_url or settings.s3_endpoint_url,
            region=legacy.s3_region or settings.s3_region,
            access_key=legacy.s3_access_key or settings.s3_access_key,
            secret_key=legacy.s3_secret_key or settings.s3_secret_key,
        )
    if provider is None:
        return S3StorageBackend()
    transport = resolve_transport(provider)
    if transport.kind is TransportKind.S3:
        return S3StorageBackend(transport=transport)
    return OpenDALStorageBackend(transport)


def finish(session: Session, config: SystemConfig) -> None:
    """Activate the persisted choice and bind it for this process."""
    runtime_config.activate_config(config)
    # A fresh setup owns the empty roots it just created.  Enroll them with
    # the persisted installation identity so the next startup can distinguish
    # this mount from an accidental empty shadow directory.
    if settings.storage_backend == "local":
        # First-run setup is the sole flow allowed to provision managed roots.
        ensure_dirs(create_managed_roots=True)
        from app.modules.storage.storage_backend.local import (
            LocalStorageBackend,
            enroll_legacy_local_root,
        )
        from app.modules.storage.storage_backend.runtime import bind_backend

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
                raise _enrollment_failed()

        # Startup deliberately bound a recovery-mode adapter while these roots
        # were still unowned. Replace that snapshot now so the first upload
        # after setup works without requiring a process restart.
        active_backend = LocalStorageBackend()
        active_backend.ensure_setup()
        if active_backend.recovery_mode:
            raise _enrollment_failed()
        bind_backend(active_backend)
    else:
        from app.modules.storage.storage_backend.runtime import bind_backend
        from app.modules.storage.storage_providers import parse_provider_config

        provider = (
            parse_provider_config(json.loads(str(settings.storage_provider_config)))
            if settings.storage_provider_config
            else None
        )
        backend = remote_backend(provider)
        backend.ensure_setup()
        if not backend.capabilities.conditional_create:
            raise OperationError(
                "setup_remote_storage_unavailable", kind=ErrorKind.CONFLICT
            )
        bind_backend(backend)

    config.setup_storage_pending = False
    session.add(config)
    session.commit()
