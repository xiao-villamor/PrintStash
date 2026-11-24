"""Typed configuration boundary for reusable remote-storage connections."""

from __future__ import annotations

import json
from typing import Mapping

from app.db.models import LibrarySourceKind, StorageConnection
from app.modules.storage.storage_providers import (
    StorageProviderConfig,
    resolve_transport,
    split_provider_config,
)
from app.modules.storage.transports import remote_transport
from app.modules.storage.transports.base import RemoteTransport, TransportKind


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


def connection_transport(kind: LibrarySourceKind) -> RemoteTransport:
    """The remote transport a connection row of ``kind`` uses."""
    if kind is LibrarySourceKind.MOUNTED:
        raise StorageConnectionConfigError("storage_connection_invalid")
    return remote_transport(TransportKind(kind.value))


def parse_connection_config(
    kind: LibrarySourceKind,
    configuration: Mapping[str, object],
    secrets: Mapping[str, str],
) -> StorageProviderConfig:
    registered = connection_transport(kind)
    merged: dict[str, object] = {**configuration, **secrets}
    provider = merged.pop("provider", registered.default_provider)
    try:
        return registered.config_model.model_validate({"provider": provider, **merged})
    except ValueError as exc:
        raise StorageConnectionConfigError("storage_connection_invalid") from exc


def serialize_connection_config(
    kind: LibrarySourceKind,
    configuration: Mapping[str, object],
    secrets: Mapping[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    parsed = parse_connection_config(kind, configuration, secrets)
    try:
        resolve_transport(parsed)
    except ValueError as exc:
        raise StorageConnectionConfigError("storage_connection_invalid") from exc
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


def connection_target_signature(kind, configuration, secrets) -> dict[str, object]:
    """Credential-free resolved locator used only to guard profile edits.

    Unlike failure-domain identity, this includes the namespace and account name:
    another prefix or login must not redirect already linked source objects.
    """
    from app.modules.storage.storage_providers import (
        _secret_field_names,
        resolve_transport,
    )

    parsed = parse_connection_config(kind, configuration, secrets)
    spec = resolve_transport(parsed)
    ignored = _secret_field_names(parsed) | {"host_key", "private_key_path"}
    return {
        "transport": spec.kind.value,
        "namespace": spec.namespace,
        "options": {
            key: value for key, value in spec.options.items() if key not in ignored
        },
    }
