"""Conservative failure-domain evidence for storage target identities.

Locator hashes remain unchanged. Neither a role, a namespace prefix, a profile,
nor different credentials establish independent storage.
"""

from __future__ import annotations

import ipaddress
import json
import re

from app.modules.storage.target_identity import (
    StorageTargetIdentity,
    locator_endpoint,
    normalized_endpoint,
)
from app.modules.storage.transports import transport
from app.modules.storage.transports.base import TransportKind
from app.modules.storage.transports.s3 import s3_target

__all__ = [
    "StorageTargetIdentity",
    "identity_evidence",
    "independent_evidence",
    "locator_endpoint",
    "normalized_endpoint",
    "provider_domain",
    "s3_target",
    "shares_storage",
    "target_for_transport",
]


def provider_domain(target: StorageTargetIdentity) -> str | None:
    """A provider-wide failure domain shared by every target the provider hosts."""
    try:
        kind = TransportKind(target.transport)
    except ValueError:
        # Persisted identities may name a transport this build no longer
        # registers; it shares no provider-wide failure domain.
        return None
    return transport(kind).provider_domain(target)


def target_for_transport(kind: str, options: dict) -> StorageTargetIdentity | None:
    try:
        registered = transport(TransportKind(kind))
    except ValueError:
        return None
    return registered.target(options)


def shares_storage(first: StorageTargetIdentity, second: StorageTargetIdentity) -> bool:
    if first.target_ref == second.target_ref:
        return True
    if first.host and first.host == second.host:
        return True
    if provider_domain(first) and provider_domain(first) == provider_domain(second):
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

    domain = provider_domain(target)
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
            except TypeError, ValueError:
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
