"""Compatibility façade for shared outbound URL safety helpers."""

from printstash_core import networking as _networking
from printstash_core.networking import (
    PinnedTarget,
    UnsafeUrlError,
    is_public_ip,
    normalize_http_url,
    pinned_sync_transport,
    pinned_transport,
)


def resolve_public_target(url: str) -> PinnedTarget:
    """Resolve through the shared guard while preserving the legacy test seam."""
    return _networking.resolve_public_target(url, ip_validator=is_public_ip)


def is_public_url(url: str) -> bool:
    """Return whether a URL resolves only to allowed public addresses."""
    return _networking.is_public_url(url, ip_validator=is_public_ip)


__all__ = [
    "PinnedTarget",
    "UnsafeUrlError",
    "is_public_ip",
    "is_public_url",
    "normalize_http_url",
    "pinned_sync_transport",
    "pinned_transport",
    "resolve_public_target",
]
