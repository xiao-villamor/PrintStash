"""Redaction helpers for untrusted provider URLs and errors.

Provider pages and APIs are allowed to return signed URLs and arbitrary error
messages.  Those values may be useful while handling a request, but they are
not safe to put in logs or durable diagnostics.  Keep this module small and
dependency-free so every provider boundary can use the same policy.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEY_NAMES = frozenset(
    {
        "accesskey",
        "accessid",
        "accesstoken",
        "apikey",
        "apikeyid",
        "api_key",
        "authorization",
        "auth",
        "clientcredential",
        "clientsecret",
        "credential",
        "idtoken",
        "key",
        "keyid",
        "password",
        "privatekey",
        "refreshkey",
        "refreshtoken",
        "secret",
        "securitytoken",
        "signature",
        "sig",
        "token",
        "xamzcredential",
        "xamzsecuritytoken",
        "xamzsignature",
    }
)
_SENSITIVE_KEY_MARKERS = (
    "accesskey",
    "accessid",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
)
_KEY_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def normalize_query_key(value: str) -> str:
    """Normalize a query key for case- and punctuation-insensitive matching."""

    return _KEY_PUNCTUATION.sub("", value.casefold())


def is_sensitive_query_key(value: str) -> bool:
    """Return whether a query key is a known credential/signature variant."""

    normalized = normalize_query_key(value)
    return normalized in _SENSITIVE_KEY_NAMES or any(
        marker in normalized for marker in _SENSITIVE_KEY_MARKERS
    )


def redact_url(value: str) -> str:
    """Return a URL safe for logs and diagnostics.

    Credential-bearing query fields are removed (rather than replaced), URL
    userinfo is removed, and fragments are never retained.  Invalid or
    relative values return a fixed marker so a malformed value cannot bypass
    the query parser and leak into a log line.
    """

    try:
        parts = urlsplit(value.strip())
        if not parts.scheme or not parts.netloc or parts.hostname is None:
            return "[redacted-url]"
        hostname = parts.hostname
        try:
            hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return "[redacted-url]"
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = parts.port
        netloc = hostname if port is None else f"{hostname}:{port}"
        safe_query = [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not is_sensitive_query_key(key)
        ]
        return urlunsplit(
            (
                parts.scheme.lower(),
                netloc,
                parts.path or "/",
                urlencode(safe_query),
                "",
            )
        )
    except (TypeError, ValueError, UnicodeError):
        return "[redacted-url]"


def redact_exception(error: BaseException) -> str:
    """Return a stable, message-free description of an upstream exception."""

    # Exception messages commonly contain signed URLs, response bodies, and
    # credentials.  The class name is local code, not upstream text.
    return type(error).__name__


__all__ = [
    "is_sensitive_query_key",
    "normalize_query_key",
    "redact_exception",
    "redact_url",
]
