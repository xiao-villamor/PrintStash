"""Reusable, encrypted connection profiles for remote library sources."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.security import require_superuser
from app.db.models import ExternalLibrary, LibrarySourceKind, StorageConnection
from app.db.session import get_session
from app.services.library_source import LibrarySourceError, source_from_connection
from app.services.storage_providers import (
    S3ProviderConfig,
    SFTPProviderConfig,
    WebDAVProviderConfig,
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
    configuration: dict[str, object]
    secrets: dict[str, str] = Field(default_factory=dict)


class StorageConnectionRead(BaseModel):
    id: int
    name: str
    kind: LibrarySourceKind
    configuration: dict[str, object]
    secret_fields_set: list[str]
    enabled: bool


_SECRET_FIELDS = {
    LibrarySourceKind.S3: {"access_key", "secret_key"},
    LibrarySourceKind.WEBDAV: {"password"},
    LibrarySourceKind.SFTP: {"password", "passphrase"},
}


def _validated(
    kind: LibrarySourceKind,
    configuration: dict[str, object],
    secrets: dict[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    allowed_secrets = _SECRET_FIELDS.get(kind, set())
    if set(secrets) - allowed_secrets or set(configuration) & allowed_secrets:
        raise HTTPException(status_code=400, detail="storage_connection_secret_invalid")
    merged = {**configuration, **secrets}
    try:
        if kind == LibrarySourceKind.S3:
            parsed = S3ProviderConfig.model_validate(
                {"provider": merged.pop("provider", "s3"), **merged}
            )
        elif kind == LibrarySourceKind.WEBDAV:
            parsed = WebDAVProviderConfig.model_validate(
                {"provider": merged.pop("provider", "webdav"), **merged}
            )
        elif kind == LibrarySourceKind.SFTP:
            parsed = SFTPProviderConfig.model_validate(
                {"provider": "sftp", **merged}
            )
        else:
            raise ValueError("mounted connections are not reusable profiles")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="storage_connection_invalid") from exc
    raw = parsed.model_dump(mode="json")
    clean_secrets = {
        name: str(raw.pop(name))
        for name in tuple(raw)
        if name in allowed_secrets and raw[name] not in (None, "")
    }
    return raw, clean_secrets


def _read(row: StorageConnection) -> StorageConnectionRead:
    assert row.id is not None
    configuration = json.loads(row.config_json or "{}")
    secret_fields = sorted(json.loads(row.secret_json or "{}"))
    return StorageConnectionRead(
        id=row.id,
        name=row.name,
        kind=row.kind,
        configuration=configuration,
        secret_fields_set=secret_fields,
        enabled=row.enabled,
    )


@router.get("", response_model=list[StorageConnectionRead])
def list_connections(session: Session = Depends(get_session)) -> list[StorageConnectionRead]:
    rows = session.exec(
        select(StorageConnection).order_by(StorageConnection.name.asc())  # type: ignore[attr-defined]
    ).all()
    return [_read(row) for row in rows]


@router.post("", response_model=StorageConnectionRead, status_code=201)
def create_connection(
    body: StorageConnectionCreate, session: Session = Depends(get_session)
) -> StorageConnectionRead:
    if session.exec(
        select(StorageConnection.id).where(StorageConnection.name == body.name.strip())
    ).first() is not None:
        raise HTTPException(status_code=409, detail="storage_connection_name_in_use")
    configuration, secrets = _validated(body.kind, body.configuration, body.secrets)
    row = StorageConnection(
        name=body.name.strip(),
        kind=body.kind,
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
        page = source_from_connection(row, scan_limits=True).list_page(
            "", cursor=None, limit=1
        )
    except LibrarySourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "sample_count": len(page.entries)}


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int, session: Session = Depends(get_session)
) -> Response:
    row = session.get(StorageConnection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="storage_connection_not_found")
    if session.exec(
        select(ExternalLibrary.id).where(ExternalLibrary.connection_id == connection_id)
    ).first() is not None:
        raise HTTPException(status_code=409, detail="storage_connection_in_use")
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
