"""The versioned identity of a storage destination, independent of transports.

Locator hashes remain unchanged. Transports build these identities; the
failure-domain rules that compare them live in ``storage_identity``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict


class StorageTargetIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    transport: str
    endpoint: str
    container: str = ""
    account: str = ""

    @property
    def target_ref(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def host(self) -> str:
        return (urlsplit(self.endpoint).hostname or "").lower().rstrip(".")


def normalized_endpoint(value: str) -> str:
    parts = urlsplit(value.strip())
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("storage_target_endpoint_invalid")
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https", "sftp"}:
        raise ValueError("storage_target_endpoint_invalid")
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port and port != {"http": 80, "https": 443, "sftp": 22}[scheme]:
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path.rstrip("/"), "", ""))


def locator_endpoint(value: object) -> str:
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
