"""Remote backup replicas backed by reusable storage connections."""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Iterator

from sqlmodel import select

from app.db.models import (
    OwnedStorageObject,
    StorageConnection,
    StorageConnectionPurpose,
    StorageObjectState,
)
from app.db.session import get_session_factory
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_connections import (
    StorageConnectionConfigError,
    load_connection_config,
)
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_ownership import provider_ref_for_backend
from app.services.storage_providers import resolve_transport

BACKUP_PREFIX = "printstash-backups"
logger = logging.getLogger(__name__)


class BackupTrigger(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class BackupDestinationError(RuntimeError):
    """A configured remote backup destination cannot satisfy an operation."""


@dataclass(frozen=True)
class RemoteBackupDestination:
    connection_id: int
    name: str
    provider: str
    backend: OpenDALStorageBackend
    provider_ref: str

    @property
    def namespace(self) -> str:
        return self.backend.source_namespace

    @property
    def location(self) -> str:
        return f"opendal:{self.provider}"

    def key(self, archive_name: str) -> str:
        return self.backend.source_key(f"{BACKUP_PREFIX}/{archive_name}")

    def probe(self) -> dict[str, object]:
        try:
            self.backend.check()
        except Exception as exc:
            raise BackupDestinationError("storage_connection_probe_failed") from exc
        capabilities = self.backend.operator_capabilities
        return {
            "ok": True,
            "provider": self.provider,
            "read": bool(getattr(capabilities, "read", False)),
            "write": bool(getattr(capabilities, "write", False)),
            "conditional_create": bool(
                getattr(capabilities, "write_with_if_not_exists", False)
            ),
            "versioned_delete": bool(
                getattr(capabilities, "delete_with_version", False)
            ),
        }

    def require_owned(self, row: OwnedStorageObject) -> None:
        if (
            row.backend != self.backend.backend_name
            or row.namespace != self.namespace
            or row.provider_ref != self.provider_ref
            or row.state != StorageObjectState.COMMITTED
            or row.size_bytes is None
            or row.sha256 is None
        ):
            raise BackupDestinationError("backup_storage_ownership_unverified")
        info = self.backend.object_info(row.key)
        if info is None or info.size != row.size_bytes:
            raise BackupDestinationError("backup_remote_identity_mismatch")
        if row.etag and info.etag != row.etag:
            raise BackupDestinationError("backup_remote_identity_mismatch")
        if row.version_id and info.version_id != row.version_id:
            raise BackupDestinationError("backup_remote_identity_mismatch")

    @contextmanager
    def open_owned(self, row: OwnedStorageObject) -> Iterator[BinaryIO]:
        self.require_owned(row)
        with self.backend.open_reader(row.key) as reader:
            yield reader
        self.require_owned(row)

    def download_owned(self, row: OwnedStorageObject, destination: Path) -> None:
        self.require_owned(row)
        digest = hashlib.sha256()
        written = 0
        with destination.open("xb") as output:
            for chunk in self.backend.stream_chunks(row.key):
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
        self.require_owned(row)
        if written != row.size_bytes or (
            row.sha256 and digest.hexdigest() != row.sha256
        ):
            destination.unlink(missing_ok=True)
            raise BackupDestinationError("backup_download_digest_mismatch")

    def delete_owned(self, row: OwnedStorageObject) -> bool:
        """Delete only through an immutable version identity.

        Consumer-cloud and WebDAV transports deliberately return ``False``;
        their path-only delete can race a replacement and is therefore never
        used by automatic retention.
        """
        self.require_owned(row)
        if not row.version_id:
            return False
        try:
            self.backend.delete_versioned(row.key, row.version_id)
        except StorageConfigurationError:
            return False
        return True


def destination_from_connection(
    connection: StorageConnection, *, require_enabled: bool = True
) -> RemoteBackupDestination:
    if require_enabled and not connection.enabled:
        raise BackupDestinationError("storage_connection_disabled")
    if not connection.purpose.allows(StorageConnectionPurpose.BACKUP):
        raise BackupDestinationError("storage_connection_not_backup")
    try:
        parsed = load_connection_config(connection)
        backend = OpenDALStorageBackend(resolve_transport(parsed))
    except StorageConfigurationError as exc:
        if str(exc) == "gdrive_transport_unavailable":
            raise BackupDestinationError(str(exc)) from exc
        raise BackupDestinationError("storage_connection_invalid") from exc
    except (StorageConnectionConfigError, ValueError, RuntimeError) as exc:
        raise BackupDestinationError("storage_connection_invalid") from exc
    backend.backend_name = f"backup-opendal-{connection.kind.value}"
    if connection.id is None:
        raise BackupDestinationError("storage_connection_invalid")
    # Bind durable ownership to the saved profile as well as its endpoint/root.
    # This distinguishes two OAuth accounts whose public Google Drive settings
    # are otherwise identical, without ever hashing or persisting a secret.
    transport_ref = provider_ref_for_backend(
        backend, namespace=backend.source_namespace
    )
    provider_ref = hashlib.sha256(
        f"{transport_ref}\x1fconnection:{connection.id}".encode()
    ).hexdigest()
    return RemoteBackupDestination(
        connection_id=connection.id,
        name=connection.name,
        provider=connection.kind.value,
        backend=backend,
        provider_ref=provider_ref,
    )


def configured_destinations(
    trigger: BackupTrigger | None = None,
) -> list[RemoteBackupDestination]:
    with get_session_factory().scoped_session() as session:
        statement = select(StorageConnection).where(
            StorageConnection.purpose.in_(
                [
                    StorageConnectionPurpose.BACKUP,
                    StorageConnectionPurpose.BOTH,
                ]
            ),
            StorageConnection.enabled.is_(True),
        )
        if trigger is not None:
            selected = (
                StorageConnection.manual_backup_enabled
                if trigger is BackupTrigger.MANUAL
                else StorageConnection.automatic_backup_enabled
            )
            statement = statement.where(selected.is_(True))
        rows = session.exec(
            statement.order_by(StorageConnection.id.asc())  # type: ignore[attr-defined]
        ).all()
        # Encrypted values are materialized while the session is alive.
        snapshots = []
        for row in rows:
            row.config_json = str(row.config_json)
            row.secret_json = str(row.secret_json)
            snapshots.append(row)
        destinations: list[RemoteBackupDestination] = []
        for row in snapshots:
            try:
                destinations.append(destination_from_connection(row))
            except BackupDestinationError:
                logger.warning(
                    "backup destination %s is unavailable",
                    row.name,
                    exc_info=True,
                )
        return destinations


def destination_for_ownership(
    row: OwnedStorageObject,
) -> RemoteBackupDestination | None:
    for destination in configured_destinations():
        if (
            destination.backend.backend_name == row.backend
            and destination.namespace == row.namespace
            and destination.provider_ref == row.provider_ref
        ):
            return destination
    return None
