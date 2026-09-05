"""Versioned target identity and conservative failure-domain evidence.

Locator hashes remain unchanged. Neither a role, a namespace prefix, a profile,
nor different credentials establish independent storage.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
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

    @property
    def provider_domain(self) -> str | None:
        if self.transport == "local":
            return f"installation:{self.endpoint}"
        if self.transport == "gdrive":
            return "provider:google"
        if self.transport != "s3":
            return None
        host = self.host
        if host in {"s3.amazonaws.com", "s3.amazonaws.com.cn"}:
            return "provider:aws"
        if re.fullmatch(r"s3[.-][a-z0-9.-]+\.amazonaws\.com(?:\.cn)?", host):
            return "provider:aws"
        if re.fullmatch(r"s3\.[a-z0-9-]+\.backblazeb2\.com", host):
            return "provider:backblaze"
        if re.fullmatch(r"s3(?:\.[a-z0-9-]+)?\.wasabisys\.com", host):
            return "provider:wasabi"
        if re.fullmatch(r"[a-z0-9-]+\.r2\.cloudflarestorage\.com", host):
            return "provider:cloudflare"
        return None


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


def s3_target(*, endpoint: str, bucket: str) -> StorageTargetIdentity:
    endpoint = normalized_endpoint(endpoint or "https://s3.amazonaws.com")
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    # Only recognize AWS's documented virtual-host endpoint. A custom server
    # named "bucket.example.test" must retain its host identity when another
    # bucket on that same server is considered as a backup witness.
    if (
        bucket
        and host.startswith(f"{bucket}.")
        and (
            StorageTargetIdentity(
                transport="s3", endpoint=f"https://{host[len(bucket) + 1 :]}"
            ).provider_domain
            == "provider:aws"
        )
    ):
        host = host[len(bucket) + 1 :]
        endpoint = urlunsplit(
            (
                parts.scheme,
                host + (f":{parts.port}" if parts.port else ""),
                parts.path,
                "",
                "",
            )
        )
    target = StorageTargetIdentity(transport="s3", endpoint=endpoint, container=bucket)
    if target.provider_domain == "provider:aws":
        endpoint = (
            "https://s3.amazonaws.com.cn"
            if host.endswith(".cn")
            else "https://s3.amazonaws.com"
        )
        return target.model_copy(update={"endpoint": endpoint})
    return target


def target_for_transport(kind: str, options: dict) -> StorageTargetIdentity | None:
    if kind == "s3":
        return s3_target(
            endpoint=str(options.get("endpoint_url") or ""),
            bucket=str(options["bucket"]),
        )
    if kind == "webdav":
        return StorageTargetIdentity(
            transport=kind, endpoint=normalized_endpoint(str(options["endpoint_url"]))
        )
    if kind == "sftp":
        host = str(options["host"])
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        endpoint = normalized_endpoint(f"sftp://{host}:{options.get('port', 22)}")
        return StorageTargetIdentity(transport=kind, endpoint=endpoint)
    if kind == "gdrive":
        # Credentials are not a durable account identity. Conservatively group
        # all Drive targets until the authenticated account can be established.
        return StorageTargetIdentity(
            transport=kind, endpoint="https://www.googleapis.com"
        )
    return None


def shares_storage(first: StorageTargetIdentity, second: StorageTargetIdentity) -> bool:
    if first.target_ref == second.target_ref:
        return True
    if first.host and first.host == second.host:
        return True
    if first.provider_domain and first.provider_domain == second.provider_domain:
        return True
    return (first.transport == "local" and _is_loopback(second.host)) or (
        second.transport == "local" and _is_loopback(first.host)
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped.is_loopback
        return address.is_loopback
    except ValueError:
        return False


def identity_evidence(target: StorageTargetIdentity | None) -> dict | None:
    """Resolve current evidence; unknown historical identity stays unknown."""
    if target is None:
        return None
    from app.db.models import StorageFailureDomainDeclaration
    from app.db.session import get_session_factory

    domain = target.provider_domain
    revision = None
    if domain is None:
        with get_session_factory().scoped_session() as session:
            declaration = session.get(
                StorageFailureDomainDeclaration, target.target_ref
            )
            if declaration is None:
                return None
            try:
                declared_target = json.loads(declaration.target_identity)
            except (TypeError, ValueError):
                return None
            if (
                declared_target != target.model_dump()
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]{0,127}", declaration.failure_domain
                )
                or not re.fullmatch(r"[0-9a-f]{32}", declaration.revision)
            ):
                return None
            domain = f"administrator:{declaration.failure_domain}"
            revision = declaration.revision
    return {
        "version": 1,
        "target": target.model_dump(),
        "failure_domain": domain,
        "declaration_revision": revision,
    }


def independent_evidence(
    active: StorageTargetIdentity | None, replica: StorageTargetIdentity | None
) -> tuple[dict, dict] | None:
    if active is None or replica is None or shares_storage(active, replica):
        return None
    active_evidence = identity_evidence(active)
    replica_evidence = identity_evidence(replica)
    if active_evidence is None or replica_evidence is None:
        return None
    if active_evidence["failure_domain"] == replica_evidence["failure_domain"]:
        return None
    return active_evidence, replica_evidence
