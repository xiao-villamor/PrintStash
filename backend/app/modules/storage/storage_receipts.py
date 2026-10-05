"""Exact storage receipts and credential-free provider identity.

These contracts depend on storage evidence, never publication or deletion orchestration.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings
from app.db.models import OwnedStorageObject
from app.modules.storage.remote_io import RemoteIO
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
)


class UnsafeStorageDeleteError(RuntimeError):
    """The exact target could not be positively and currently proven owned."""


def _normalized_endpoint(value: object) -> str:
    """Normalize an endpoint without retaining userinfo or query secrets."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError("storage_provider_endpoint_invalid") from None
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("storage_provider_endpoint_invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("storage_provider_endpoint_invalid")
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("storage_provider_endpoint_invalid") from None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port in {80, 443} and (
        (port == 80 and parsed.scheme.lower() == "http")
        or (port == 443 and parsed.scheme.lower() == "https")
    ):
        netloc = host
    else:
        netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


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
    if transport == "s3":
        if options.get("endpoint_url") is not None:
            payload["endpoint"] = _normalized_endpoint(options.get("endpoint_url"))
        if options.get("region") is not None:
            payload["region"] = str(options.get("region") or "").strip().lower()
        if options.get("addressing_style") is not None:
            payload["addressing_style"] = (
                str(options.get("addressing_style")).strip().lower()
            )
        payload["namespace"] = str(resolved_namespace)
    elif transport == "webdav" or name in {"webdav", "nextcloud"}:
        # OpenDAL's namespace is only the managed root. The endpoint is the
        # actual destination and must be included, while credentials remain
        # deliberately absent from the identity.
        webdav_endpoint = getattr(backend, "_webdav_endpoint", None)
        if webdav_endpoint is None:
            webdav_endpoint = options.get("endpoint_url")
        payload["endpoint"] = _normalized_endpoint(webdav_endpoint)
        payload["root"] = (
            str(
                getattr(backend, "_webdav_root", None)
                or options.get("root")
                or resolved_namespace
            )
            .strip()
            .strip("/")
        )
    elif transport == "sftp" or name == "sftp":
        # SFTP has no URL endpoint. Pin the network destination and managed
        # root, excluding password/private-key/passphrase material (and any
        # future option whose name advertises it is secret).
        host = str(options.get("host", getattr(backend, "_host", ""))).strip()
        payload["sftp"] = {
            "host": host.lower().rstrip("."),
            "port": int(options.get("port", getattr(backend, "_port", 22)) or 22),
            "username": str(
                options.get("username", getattr(backend, "_username", ""))
            ).strip(),
            "root": str(options.get("root") or resolved_namespace).strip().strip("/"),
        }
    elif transport == "gdrive" or name in {"gdrive", "backup-gdrive"}:
        # The OAuth client id identifies the configured Google application but
        # grants no access by itself. Refresh/access tokens and the client
        # secret remain excluded from durable locators.
        payload["gdrive"] = {
            "client_id": str(options.get("client_id") or "").strip(),
            "root": str(options.get("root") or resolved_namespace).strip().strip("/"),
        }
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
