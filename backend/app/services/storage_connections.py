"""Typed configuration boundary for reusable remote-storage connections."""

from __future__ import annotations

import json
from typing import Mapping

from app.db.models import LibrarySourceKind, StorageConnection
from app.services.storage_providers import (
    GoogleDriveProviderConfig,
    S3ProviderConfig,
    SFTPProviderConfig,
    StorageProviderConfig,
    WebDAVProviderConfig,
    split_provider_config,
)


class StorageConnectionConfigError(ValueError):
    """A connection row or submitted configuration is invalid."""


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError) as exc:
        raise StorageConnectionConfigError("storage_connection_invalid") from exc
    if not isinstance(parsed, dict):
        raise StorageConnectionConfigError("storage_connection_invalid")
    return {str(key): item for key, item in parsed.items()}


def parse_connection_config(
    kind: LibrarySourceKind,
    configuration: Mapping[str, object],
    secrets: Mapping[str, str],
) -> StorageProviderConfig:
    merged: dict[str, object] = {**configuration, **secrets}
    try:
        if kind == LibrarySourceKind.S3:
            return S3ProviderConfig.model_validate(
                {"provider": merged.pop("provider", "s3"), **merged}
            )
        if kind == LibrarySourceKind.WEBDAV:
            return WebDAVProviderConfig.model_validate(
                {"provider": merged.pop("provider", "webdav"), **merged}
            )
        if kind == LibrarySourceKind.SFTP:
            return SFTPProviderConfig.model_validate({"provider": "sftp", **merged})
        if kind == LibrarySourceKind.GDRIVE:
            return GoogleDriveProviderConfig.model_validate(
                {"provider": "gdrive", **merged}
            )
    except ValueError as exc:
        raise StorageConnectionConfigError("storage_connection_invalid") from exc
    raise StorageConnectionConfigError("storage_connection_invalid")


def serialize_connection_config(
    kind: LibrarySourceKind,
    configuration: Mapping[str, object],
    secrets: Mapping[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    parsed = parse_connection_config(kind, configuration, secrets)
    return split_provider_config(parsed)


def load_connection_config(connection: StorageConnection) -> StorageProviderConfig:
    return parse_connection_config(
        connection.kind,
        _json_object(connection.config_json),
        {
            str(key): str(value)
            for key, value in _json_object(connection.secret_json).items()
        },
    )
