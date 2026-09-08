"""Select authorized Artifact delivery without leaking provider choices to routes.

Callers authorize before entering this module. External content retains its
verify-before-exposure boundary; generated representations never redirect.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from app.core.time import ensure_utc, utcnow
from app.db.models import File
from app.modules.storage.artifact_content import ArtifactContentMissingError, resolve
from app.modules.storage.delivery_contracts import BrowserDownload, content_disposition
from app.modules.storage.storage_backend.contracts import (
    StorageBackend,
    StorageObjectInfo,
)

PRIVATE_REVALIDATION = "private, max-age=0, must-revalidate"
PRIVATE_NO_STORE = "private, no-store"
MAX_REDIRECT_SECONDS = 60


class DeliveryPurpose(StrEnum):
    DOWNLOAD = "download"
    BROWSER_FETCH = "browser_fetch"
    THUMBNAIL = "thumbnail"
    SLICER = "slicer"
    PUBLIC_SHARE = "public_share"
    TRANSFORMED = "transformed"


@dataclass(frozen=True)
class DeliveryRequest:
    filename: str
    media_type: str = "application/octet-stream"
    purpose: DeliveryPurpose = DeliveryPurpose.DOWNLOAD
    origin: str | None = None
    application_origin: str | None = None
    if_none_match: str | None = None
    if_modified_since: str | None = None
    range_header: str | None = None
    if_range: str | None = None
    proxy_only: bool = False
    inline: bool = False


@dataclass
class DeliveryPlan:
    status: int
    headers: dict[str, str]
    media_type: str
    path: Path | None = None
    chunks: Iterator[bytes] | None = None
    redirect: str | None = field(default=None, repr=False)
    close: Callable[[], None] | None = field(default=None, repr=False)


def cache_policy(purpose: DeliveryPurpose) -> str:
    return (
        PRIVATE_NO_STORE
        if purpose in {DeliveryPurpose.SLICER, DeliveryPurpose.PUBLIC_SHARE}
        else PRIVATE_REVALIDATION
    )


def parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def etag_matches(value: str, etag: str) -> bool:
    """Weak comparison is correct for GET's If-None-Match."""
    return any(
        candidate.strip().removeprefix("W/") in {"*", etag.removeprefix("W/")}
        for candidate in value.split(",")
    )


def representation_headers(
    request: DeliveryRequest, *, etag: str, modified_at: datetime | None
) -> dict[str, str]:
    headers = {
        "Cache-Control": cache_policy(request.purpose),
        "ETag": etag,
        "Content-Disposition": content_disposition(
            request.filename, inline=request.inline
        ),
        "Referrer-Policy": "no-referrer",
        "Vary": "Authorization, Cookie, Origin, Sec-Fetch-Mode",
    }
    if modified_at is not None:
        headers["Last-Modified"] = format_datetime(
            ensure_utc(modified_at).replace(microsecond=0), usegmt=True
        )
    return headers


def not_modified(
    request: DeliveryRequest, *, etag: str, modified_at: datetime | None
) -> bool:
    if request.if_none_match is not None:
        return etag_matches(request.if_none_match, etag)
    since = parse_http_date(request.if_modified_since)
    return (
        since is not None
        and modified_at is not None
        and ensure_utc(modified_at).replace(microsecond=0) <= since
    )


def range_matches(
    request: DeliveryRequest, *, etag: str, modified_at: datetime | None
) -> bool:
    if request.if_range is None:
        return True
    if request.if_range.startswith('"'):
        return request.if_range == etag and not etag.startswith("W/")
    since = parse_http_date(request.if_range)
    return (
        since is not None
        and modified_at is not None
        and ensure_utc(modified_at).replace(microsecond=0) == since
    )


def byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Return one inclusive range; unsupported/malformed ranges are ignored."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value or "")
    if match is None or not any(match.groups()):
        return None
    first, last = match.groups()
    # Bound integer parsing independently from Python's interpreter limit.
    if len(first) > 20 or len(last) > 20:
        raise ValueError("range_unsatisfiable")
    if not first:
        suffix = int(last)
        if not suffix or not size:
            raise ValueError("range_unsatisfiable")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError("range_unsatisfiable")
    return start, end


def safe_browser_download(
    target: BrowserDownload,
    *,
    key: str,
    origin: str | None,
    application_origin: str | None = None,
    now: datetime,
) -> bool:
    try:
        url = urlsplit(target.url)
        app_url = urlsplit(application_origin) if application_origin else None
        target_origin = (url.scheme.lower(), url.hostname, url.port or 443)
        app_origin = (
            (app_url.scheme.lower(), app_url.hostname, app_url.port or 443)
            if app_url is not None
            else None
        )
        remaining = (ensure_utc(target.expires_at) - ensure_utc(now)).total_seconds()
        return (
            url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.fragment
            and not any(ord(char) <= 32 or ord(char) == 127 for char in target.url)
            and target.method == "GET"
            and not target.required_headers
            and target.key == key
            and 0 < remaining <= MAX_REDIRECT_SECONDS
            and (origin is None or target.cors_origin == origin)
            # Browsers and HTTP clients may retain Authorization across a
            # same-origin redirect. Provider targets must never receive the
            # PrintStash bearer credential, even when an S3 endpoint is reverse
            # proxied below the application's own host.
            and target_origin != app_origin
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _plan_artifact(artifact: File, request: DeliveryRequest) -> DeliveryPlan:
    """Select the original Artifact representation after the caller's access check."""
    handle = resolve(artifact)
    backend = handle.backend
    path = backend.direct_path(artifact.path) if backend is not None else None
    if path is not None and not path.is_file():
        raise ArtifactContentMissingError("file_blob_missing")
    etag = f'"{artifact.sha256.lower()}"'
    modified = artifact.uploaded_at
    headers = representation_headers(request, etag=etag, modified_at=modified)
    if not_modified(request, etag=etag, modified_at=modified):
        return DeliveryPlan(304, headers, request.media_type)
    can_redirect = request.purpose in {
        DeliveryPurpose.DOWNLOAD,
        DeliveryPurpose.BROWSER_FETCH,
        DeliveryPurpose.THUMBNAIL,
    }
    # A provider's object ETag need not equal our original SHA-256 validator.
    # Keep range admission here so If-Range always names this representation.
    if (
        backend is not None
        and path is None
        and can_redirect
        and not request.proxy_only
        and not request.range_header
    ):
        target = backend.browser_download(
            artifact.path,
            request.filename,
            request.media_type,
            origin=request.origin,
            inline=request.inline,
        )
        if target is not None and safe_browser_download(
            target,
            key=artifact.path,
            origin=request.origin,
            application_origin=request.application_origin,
            now=utcnow(),
        ):
            return DeliveryPlan(
                307,
                {"Cache-Control": PRIVATE_NO_STORE, "Referrer-Policy": "no-referrer"},
                request.media_type,
                redirect=target.url,
            )
    if path is not None:
        return DeliveryPlan(200, headers, request.media_type, path=path)
    if (
        backend is not None
        and backend.supports_ranges
        and range_matches(request, etag=etag, modified_at=modified)
    ):
        headers["Accept-Ranges"] = "bytes"
        try:
            selected = byte_range(request.range_header, artifact.size_bytes)
        except ValueError:
            headers["Content-Range"] = f"bytes */{artifact.size_bytes}"
            return DeliveryPlan(416, headers, request.media_type)
        if selected is not None:
            start, end = selected
            chunks = backend.stream_range(artifact.path, start, end)
            headers["Content-Range"] = f"bytes {start}-{end}/{artifact.size_bytes}"
            headers["Content-Length"] = str(end - start + 1)
            return DeliveryPlan(
                206,
                headers,
                request.media_type,
                chunks=chunks,
                close=getattr(chunks, "close", None),
            )
    chunks = handle.stream()
    return DeliveryPlan(
        200,
        headers,
        request.media_type,
        chunks=chunks,
        close=getattr(chunks, "close", None),
    )


def _plan_stored_representation(
    backend: StorageBackend,
    key: str,
    request: DeliveryRequest,
    *,
    info: StorageObjectInfo | None = None,
) -> DeliveryPlan:
    """Serve a derived object with its own storage validator, never the source hash."""
    info = info or backend.object_info(key)
    if info is None:
        raise ArtifactContentMissingError("file_blob_missing")
    etag = info.etag
    if not etag:
        # Missing modification identity cannot validate a mutable representation.
        identity = f"{key}:{info.size}:{info.modified_at}"
        etag = f'W/"{hashlib.sha256(identity.encode()).hexdigest()}"'
    elif not etag.startswith(('"', 'W/"')):
        etag = f'"{etag}"'
    headers = representation_headers(request, etag=etag, modified_at=info.modified_at)
    if (info.etag or info.modified_at) and not_modified(
        request, etag=etag, modified_at=info.modified_at
    ):
        return DeliveryPlan(304, headers, request.media_type)
    path = backend.direct_path(key)
    if (
        path is None
        and request.purpose == DeliveryPurpose.THUMBNAIL
        and not request.proxy_only
        and not request.range_header
    ):
        target = backend.browser_download(
            key,
            request.filename,
            request.media_type,
            origin=request.origin,
            inline=request.inline,
        )
        if target is not None and safe_browser_download(
            target,
            key=key,
            origin=request.origin,
            application_origin=request.application_origin,
            now=utcnow(),
        ):
            return DeliveryPlan(
                307,
                {"Cache-Control": PRIVATE_NO_STORE, "Referrer-Policy": "no-referrer"},
                request.media_type,
                redirect=target.url,
            )
    if path is not None:
        if not path.is_file():
            raise ArtifactContentMissingError("file_blob_missing")
        return DeliveryPlan(200, headers, request.media_type, path=path)
    chunks = iter(backend.stream_chunks(key))
    return DeliveryPlan(
        200,
        headers,
        request.media_type,
        chunks=chunks,
        close=getattr(chunks, "close", None),
    )


def bytes_response_headers(
    data: bytes, request: DeliveryRequest
) -> tuple[int, dict[str, str]]:
    """Validators for freshly generated bytes belong to that representation."""
    etag = f'"{hashlib.sha256(data).hexdigest()}"'
    headers = representation_headers(request, etag=etag, modified_at=None)
    return (304 if not_modified(request, etag=etag, modified_at=None) else 200), headers


def _observe(
    plan: DeliveryPlan, request: DeliveryRequest, provider: str
) -> DeliveryPlan:
    from app.modules.storage.delivery_observability import (
        MeteredChunks,
        record_strategy,
    )

    strategy = (
        "redirect"
        if plan.redirect is not None
        else "local"
        if plan.path is not None
        else "proxy"
        if plan.chunks is not None
        else "not_modified"
        if plan.status == 304
        else "rejected"
    )
    record_strategy(provider, request.purpose.value, strategy)
    if plan.chunks is not None:
        chunks = MeteredChunks(plan.chunks, provider, request.purpose.value)
        plan.chunks = chunks
        plan.close = chunks.close
    return plan


def plan_artifact(artifact: File, request: DeliveryRequest) -> DeliveryPlan:
    from app.modules.storage.storage_backend.runtime import get_backend

    plan = _plan_artifact(artifact, request)
    provider = (
        "external"
        if artifact.is_external
        else getattr(get_backend(), "backend_name", "unknown")
    )
    return _observe(plan, request, provider)


def plan_stored_representation(
    backend: StorageBackend,
    key: str,
    request: DeliveryRequest,
    *,
    info: StorageObjectInfo | None = None,
) -> DeliveryPlan:
    return _observe(
        _plan_stored_representation(backend, key, request, info=info),
        request,
        getattr(backend, "backend_name", "unknown"),
    )
