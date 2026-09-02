"""Reusable encrypted connection profiles for remote storage."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.security import require_superuser
from app.db.models import (
    ExternalLibrary,
    LibrarySourceKind,
    OwnedStorageObject,
    StorageConnection,
    StorageConnectionPurpose,
)
from app.db.session import get_session
from app.services.backup_destination import (
    BackupDestinationError,
    destination_from_connection,
)
from app.services.library_source import LibrarySourceError, source_from_connection
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_connections import (
    StorageConnectionConfigError,
    serialize_connection_config,
)

router = APIRouter(
    prefix="/storage-connections",
    tags=["storage-connections"],
    dependencies=[Depends(require_superuser)],
)


class StorageConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    kind: LibrarySourceKind
    purpose: StorageConnectionPurpose = StorageConnectionPurpose.LIBRARY
    configuration: dict[str, object]
    secrets: dict[str, str] = Field(default_factory=dict)


class StorageConnectionRead(BaseModel):
    id: int
    name: str
    kind: LibrarySourceKind
    purpose: StorageConnectionPurpose
    configuration: dict[str, object]
    secret_fields_set: list[str]
    enabled: bool


class StorageConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    purpose: StorageConnectionPurpose | None = None


_SECRET_FIELDS = {
    LibrarySourceKind.S3: {"access_key", "secret_key"},
    LibrarySourceKind.WEBDAV: {"password"},
    LibrarySourceKind.SFTP: {"password", "passphrase"},
    LibrarySourceKind.GDRIVE: {"client_secret", "refresh_token"},
}


def _validated(
    kind: LibrarySourceKind,
    configuration: dict[str, object],
    secrets: dict[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    allowed_secrets = _SECRET_FIELDS.get(kind, set())
    if set(secrets) - allowed_secrets or set(configuration) & allowed_secrets:
        raise HTTPException(status_code=400, detail="storage_connection_secret_invalid")
    try:
        return serialize_connection_config(kind, configuration, secrets)
    except StorageConnectionConfigError as exc:
        raise HTTPException(
            status_code=400, detail="storage_connection_invalid"
        ) from exc


def _read(row: StorageConnection) -> StorageConnectionRead:
    assert row.id is not None
    configuration = json.loads(row.config_json or "{}")
    secret_fields = sorted(json.loads(row.secret_json or "{}"))
    return StorageConnectionRead(
        id=row.id,
        name=row.name,
        kind=row.kind,
        purpose=row.purpose,
        configuration=configuration,
        secret_fields_set=secret_fields,
        enabled=row.enabled,
    )


@router.get("", response_model=list[StorageConnectionRead])
def list_connections(
    session: Session = Depends(get_session),
) -> list[StorageConnectionRead]:
    rows = session.exec(
        select(StorageConnection).order_by(StorageConnection.name.asc())  # type: ignore[attr-defined]
    ).all()
    return [_read(row) for row in rows]


@router.post("", response_model=StorageConnectionRead, status_code=201)
def create_connection(
    body: StorageConnectionCreate, session: Session = Depends(get_session)
) -> StorageConnectionRead:
    if (
        session.exec(
            select(StorageConnection.id).where(
                StorageConnection.name == body.name.strip()
            )
        ).first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="storage_connection_name_in_use")
    configuration, secrets = _validated(body.kind, body.configuration, body.secrets)
    row = StorageConnection(
        name=body.name.strip(),
        kind=body.kind,
        purpose=body.purpose,
        config_json=json.dumps(configuration, sort_keys=True),
        secret_json=json.dumps(secrets, sort_keys=True),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _read(row)


@router.post("/{connection_id}/probe")
def probe_connection(
    connection_id: int, session: Session = Depends(get_session)
) -> dict[str, object]:
    row = session.get(StorageConnection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="storage_connection_not_found")
    try:
        if row.purpose.allows(StorageConnectionPurpose.BACKUP):
            return destination_from_connection(row).probe()
        page = source_from_connection(row, scan_limits=True).list_page(
            "", cursor=None, limit=1
        )
    except (
        BackupDestinationError,
        LibrarySourceError,
        StorageConfigurationError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "sample_count": len(page.entries)}


@router.patch("/{connection_id}", response_model=StorageConnectionRead)
def update_connection(
    connection_id: int,
    body: StorageConnectionUpdate,
    session: Session = Depends(get_session),
) -> StorageConnectionRead:
    row = session.get(StorageConnection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="storage_connection_not_found")
    if body.enabled is None and body.purpose is None:
        raise HTTPException(status_code=400, detail="storage_connection_update_empty")
    if body.purpose is not None and body.purpose != row.purpose:
        _assert_removed_uses_are_free(row, body.purpose, session)
        row.purpose = body.purpose
    if body.enabled is not None:
        row.enabled = body.enabled
    session.add(row)
    session.commit()
    session.refresh(row)
    return _read(row)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int, session: Session = Depends(get_session)
) -> Response:
    row = session.get(StorageConnection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="storage_connection_not_found")
    if _has_library_references(row, session):
        raise HTTPException(status_code=409, detail="storage_connection_in_use")
    if _has_backup_objects(row, session):
        raise HTTPException(status_code=409, detail="storage_connection_in_use")
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _has_library_references(row: StorageConnection, session: Session) -> bool:
    if row.id is None or not row.purpose.allows(StorageConnectionPurpose.LIBRARY):
        return False
    return (
        session.exec(
            select(ExternalLibrary.id).where(ExternalLibrary.connection_id == row.id)
        ).first()
        is not None
    )


def _has_backup_objects(row: StorageConnection, session: Session) -> bool:
    if not row.purpose.allows(StorageConnectionPurpose.BACKUP):
        return False
    destination = destination_from_connection(row, require_enabled=False)
    return (
        session.exec(
            select(OwnedStorageObject.id).where(
                OwnedStorageObject.backend == destination.backend.backend_name,
                OwnedStorageObject.namespace == destination.namespace,
                OwnedStorageObject.provider_ref == destination.provider_ref,
            )
        ).first()
        is not None
    )


def _assert_removed_uses_are_free(
    row: StorageConnection,
    next_purpose: StorageConnectionPurpose,
    session: Session,
) -> None:
    if (
        row.purpose.allows(StorageConnectionPurpose.LIBRARY)
        and not next_purpose.allows(StorageConnectionPurpose.LIBRARY)
        and _has_library_references(row, session)
    ):
        raise HTTPException(status_code=409, detail="storage_connection_in_use")
    if (
        row.purpose.allows(StorageConnectionPurpose.BACKUP)
        and not next_purpose.allows(StorageConnectionPurpose.BACKUP)
        and _has_backup_objects(row, session)
    ):
        raise HTTPException(status_code=409, detail="storage_connection_in_use")
