"""Exact storage receipts and credential-free provider identity.

These contracts depend on storage evidence, never publication or deletion orchestration.
"""

from __future__ import annotations

import hashlib
import json

from app.core.config import settings
from app.db.models import OwnedStorageObject
from app.modules.storage.remote_io import RemoteIO
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
)
from app.modules.storage.target_identity import locator_endpoint
from app.modules.storage.transports import remote_transport
from app.modules.storage.transports.base import RemoteTransport, TransportKind

# Backends without a ``transport`` attribute (legacy adapters and fakes) are
# recognized by the backend name they report.
_LEGACY_BACKEND_TRANSPORTS = {
    "webdav": TransportKind.WEBDAV,
    "nextcloud": TransportKind.WEBDAV,
    "sftp": TransportKind.SFTP,
    "gdrive": TransportKind.GDRIVE,
    "backup-gdrive": TransportKind.GDRIVE,
}


def _locator_transport(transport: str, name: str) -> RemoteTransport | None:
    try:
        kind = TransportKind(transport)
    except ValueError:
        kind = None
    if kind is None or kind is TransportKind.LOCAL:
        kind = _LEGACY_BACKEND_TRANSPORTS.get(name)
    return None if kind is None else remote_transport(kind)


class UnsafeStorageDeleteError(RuntimeError):
    """The exact target could not be positively and currently proven owned."""


_normalized_endpoint = locator_endpoint


def provider_ref_for_backend(
    backend: StorageBackend | RemoteIO, *, namespace: str | None = None
) -> str:
    """Return a stable, credential-free provider destination identity.

    The identity intentionally excludes access/secret keys so credential
    rotation does not relabel existing receipts.  Adapter-specific fields are
    read when available; local/legacy fakes still receive a deterministic
    backend+namespace identity.
    """
    value = getattr(backend, "backend_name", None)
    name = value if isinstance(value, str) and value else "unknown"
    resolved_namespace = namespace or getattr(backend, "namespace", None)
    if not resolved_namespace:
        resolved_namespace = name
    endpoint = getattr(backend, "_endpoint_url", None)
    if endpoint is None:
        endpoint = getattr(backend, "endpoint_url", None)
    if endpoint is None and name == "s3":
        endpoint = settings.s3_endpoint_url
    if endpoint is None and name == "backup-s3":
        endpoint = settings.backup_s3_endpoint_url
    region = getattr(backend, "_region", None) or getattr(backend, "region", None)
    if region is None and name == "s3":
        region = settings.s3_region
    if region is None and name == "backup-s3":
        region = settings.backup_s3_region
    payload: dict[str, object] = {
        "backend": name,
        "provider": str(getattr(backend, "provider_id", name)),
        "transport": str(getattr(backend, "transport", name)),
        "endpoint": _normalized_endpoint(endpoint),
        "region": str(region or "").strip().lower(),
        "addressing_style": str(
            getattr(backend, "_addressing_style", None)
            or getattr(backend, "addressing_style", None)
            or "path"
        )
        .strip()
        .lower(),
    }
    transport = str(getattr(backend, "transport", name)).lower()
    spec = getattr(backend, "_spec", None)
    options = getattr(spec, "options", {})
    if not isinstance(options, dict):
        options = {}
    locator = _locator_transport(transport, name)
    if locator is not None:
        payload.update(
            locator.provider_ref_fields(
                options=options, backend=backend, namespace=str(resolved_namespace)
            )
        )
    elif name not in {"s3", "backup-s3"}:
        payload["namespace"] = str(resolved_namespace)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def owned_receipt(row: OwnedStorageObject) -> CreationReceipt:
    if row.token is None or row.size_bytes is None:
        raise UnsafeStorageDeleteError("storage_ownership_incomplete")
    return CreationReceipt(
        key=row.key,
        size=row.size_bytes,
        token=row.token,
        backend=row.backend,
        namespace=row.namespace,
        etag=row.etag,
        version_id=row.version_id,
        device=row.device,
        inode=row.inode,
        ctime_ns=row.ctime_ns,
        provider_ref=row.provider_ref,
    )
