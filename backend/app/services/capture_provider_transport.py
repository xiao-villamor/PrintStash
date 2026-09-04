"""Safe, bounded HTTP transport for external metadata providers.

The adapter intentionally contains no provider credentials, payload logging, or
metrics. Provider clients pass credentials for one call and own response parsing.
"""

from __future__ import annotations

import asyncio
import logging
import random as _random
from collections.abc import Awaitable, Callable, Collection, Mapping
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import settings
from app.core.url_safety import (
    PinnedTarget,
    UnsafeUrlError,
    pinned_transport,
    resolve_public_target,
)
from app.services.provider_redaction import redact_exception, redact_url

Resolver = Callable[[str], PinnedTarget]
Sender = Callable[[PinnedTarget, httpx.Request], Awaitable[httpx.Response]]
Sleep = Callable[[float], Awaitable[None]]

_logger = logging.getLogger(__name__)
_HOST_CONCURRENCY_LIMIT = 2
_MAX_RESPONSE_BYTES = 256 * 1024

# These are deliberately process-wide.  ProviderTransport is a short-lived
# adapter (often constructed once per resolver call), while the external host
# and its pooled connections are process-wide resources.
_host_limiters: dict[str, asyncio.Semaphore] = {}
_pooled_clients: dict[tuple[str, int, str, str, int], httpx.AsyncClient] = {}


class ProviderTransportError(Exception):
    """A stable error code for callers; never carries endpoint or payload data."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        # Preserve only the final HTTP status for callers that need to retain
        # legacy status semantics (for example, GraphQL's blocked response).
        # Never retain or expose response text, headers, or request URLs.
        self.status_code = status_code


async def _send_pinned(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
    timeout = httpx.Timeout(
        timeout=settings.capture_provider_total_timeout_seconds,
        connect=settings.capture_provider_connect_timeout_seconds,
    )
    client = _get_pooled_client(target, timeout=timeout)
    # The transport owns the response stream.  ProviderTransport consumes it
    # through _read_bounded_response before returning a response to callers.
    return await client.send(request, follow_redirects=False, stream=True)


def _normalized_host(value: str) -> str:
    """Return the key used for host-wide traffic controls."""

    try:
        hostname = value.strip().rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, AttributeError):
        return value.casefold().rstrip(".")
    return hostname.casefold()


def _get_host_limiter(host: str) -> asyncio.Semaphore:
    normalized = _normalized_host(host)
    limiter = _host_limiters.get(normalized)
    if limiter is None:
        limiter = asyncio.Semaphore(_HOST_CONCURRENCY_LIMIT)
        _host_limiters[normalized] = limiter
    return limiter


def _get_pooled_client(
    target: PinnedTarget, *, timeout: httpx.Timeout
) -> httpx.AsyncClient:
    """Return the pooled client for this pinned target and event-loop life."""

    # A client is tied to the event loop that first uses it.  Include that
    # lifecycle in the key while retaining one process-wide pool manager and
    # one process-wide limiter per normalized host.  The IP is also part of the
    # key: DNS is intentionally resolved and pinned for every physical attempt.
    scheme = urlsplit(target.url).scheme.casefold()
    key = (
        _normalized_host(target.host),
        target.port,
        scheme,
        target.ip,
        id(asyncio.get_running_loop()),
    )
    client = _pooled_clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            transport=pinned_transport(target),
            timeout=timeout,
        )
        _pooled_clients[key] = client
    return client


async def close_provider_transport() -> None:
    """Close all pooled provider clients and clear their lifecycle state."""

    clients = tuple(_pooled_clients.values())
    _pooled_clients.clear()
    _host_limiters.clear()
    for client in clients:
        if not client.is_closed:
            await client.aclose()


async def _close_response(response: httpx.Response) -> None:
    """Close either a streamed response or a buffered test-boundary response."""

    if isinstance(response.stream, httpx.AsyncByteStream):
        await response.aclose()
    elif not response.is_closed:
        response.close()


async def _read_bounded_response(
    response: httpx.Response, *, max_bytes: int
) -> httpx.Response:
    """Consume a response body incrementally and retain at most ``max_bytes``."""

    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                await _close_response(response)
                raise ProviderTransportError("provider_response_too_large")
        except ValueError:
            # An invalid length header is not a reason to trust the body.  The
            # streaming loop below remains the source of truth.
            pass

    # ``Response(content=...)`` from a public test fake is already bounded by
    # construction.  Real sender responses are streamed and do not have
    # ``_content`` until this function has read them.
    if hasattr(response, "_content"):
        if len(response.content) > max_bytes:
            await _close_response(response)
            raise ProviderTransportError("provider_response_too_large")
        return response

    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                await _close_response(response)
                raise ProviderTransportError("provider_response_too_large")
            chunks.append(chunk)
    except ProviderTransportError:
        raise
    except Exception:
        # Let the caller classify the exception using httpx's public transport
        # error types.  Do not put the upstream exception text in a log.
        await _close_response(response)
        raise
    # Return a normal buffered response after the bounded stream has been
    # consumed.  Constructing a fresh response keeps this seam independent of
    # httpx's private ``_content`` implementation and closes the original
    # response before its pooled connection is reused.
    request = getattr(response, "_request", None)
    buffered = httpx.Response(
        response.status_code,
        headers=response.headers,
        content=b"".join(chunks),
        request=request,
        extensions=response.extensions,
        history=response.history,
        default_encoding=response.default_encoding,
    )
    await _close_response(response)
    return buffered


class ProviderTransport:
    """Pinned HTTP request boundary with bounded redirects, retries, and load."""

    def __init__(
        self,
        *,
        resolver: Resolver = resolve_public_target,
        sender: Sender = _send_pinned,
        sleep: Sleep = asyncio.sleep,
        random: Callable[[], float] = _random.random,
        max_attempts: int | None = None,
        concurrency: int | None = None,
        retry_after_max_seconds: float | None = None,
        max_response_bytes: int | None = None,
    ) -> None:
        self._resolver = resolver
        self._sender = sender
        self._sleep = sleep
        self._random = random
        self._max_attempts = (
            max_attempts
            if max_attempts is not None
            else settings.capture_provider_max_attempts
        )
        self._retry_after_max_seconds = (
            retry_after_max_seconds
            if retry_after_max_seconds is not None
            else settings.capture_provider_retry_after_max_seconds
        )
        self._max_response_bytes = (
            max_response_bytes
            if max_response_bytes is not None
            else int(
                getattr(
                    settings,
                    "capture_provider_max_response_bytes",
                    _MAX_RESPONSE_BYTES,
                )
            )
        )
        concurrency_limit = (
            concurrency
            if concurrency is not None
            else settings.capture_provider_concurrency
        )
        if not 1 <= self._max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 1 <= concurrency_limit <= 4:
            raise ValueError("concurrency must be between 1 and 4")
        if not 0 <= self._retry_after_max_seconds <= 10:
            raise ValueError("retry_after_max_seconds must be between 0 and 10")
        if self._max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        # ``concurrency`` remains accepted for compatibility with callers that
        # validate the old setting.  Traffic is now limited globally per host
        # by _HOST_CONCURRENCY_LIMIT, not per ProviderTransport instance.
        _ = concurrency_limit

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        allowed_hosts: Collection[str] | None = None,
    ) -> httpx.Response:
        """Send one metadata request without exposing redirects to unvalidated DNS.

        Every physical request, including a redirect hop, consumes one of at most
        three attempts. Only transient network failures, ``429``, and ``5xx``
        responses are retried.
        """
        current_url = url
        current_method = method.upper()
        current_json = json
        current_data = data
        allowed = (
            frozenset(_normalized_host(host) for host in allowed_hosts)
            if allowed_hosts
            else None
        )

        for attempt in range(self._max_attempts):
            self._ensure_allowed(current_url, allowed)
            try:
                target = self._resolver(current_url)
            except UnsafeUrlError as exc:
                raise ProviderTransportError(exc.reason) from exc
            request = httpx.Request(
                current_method,
                target.url,
                headers=headers,
                json=current_json,
                data=current_data,
            )
            if auth is not None:
                request = next(httpx.BasicAuth(*auth).auth_flow(request))
            try:
                # Acquire only for the physical upstream operation.  Backoff,
                # URL resolution, and contract failures never consume a host
                # slot, so two concurrent ProviderTransport instances share a
                # deterministic process-wide cap of two per normalized host.
                async with _get_host_limiter(target.host):
                    response = await self._sender(target, request)
                    response = await _read_bounded_response(
                        response, max_bytes=self._max_response_bytes
                    )
            except ProviderTransportError as exc:
                _logger.warning(
                    "provider response rejected url=%s code=%s",
                    redact_url(current_url),
                    exc.code,
                )
                raise
            except (httpx.NetworkError, httpx.TimeoutException):
                _logger.warning(
                    "provider upstream request failed url=%s error=network_error",
                    redact_url(current_url),
                )
                if attempt + 1 >= self._max_attempts:
                    raise ProviderTransportError(
                        "provider_retry_exhausted", retryable=True
                    ) from None
                await self._sleep(self._retry_delay(None, attempt))
                continue
            except httpx.HTTPError as exc:
                _logger.warning(
                    "provider upstream request failed url=%s error=%s",
                    redact_url(current_url),
                    redact_exception(exc),
                )
                raise ProviderTransportError("provider_transport_failed") from None

            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    await _close_response(response)
                    raise ProviderTransportError("provider_redirect_invalid")
                if attempt + 1 >= self._max_attempts:
                    await _close_response(response)
                    raise ProviderTransportError(
                        "provider_retry_exhausted", retryable=True
                    )
                current_url = urljoin(target.url, location)
                if response.status_code in {301, 302, 303} and current_method != "HEAD":
                    current_method, current_json, current_data = "GET", None, None
                await _close_response(response)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 >= self._max_attempts:
                    await _close_response(response)
                    raise ProviderTransportError(
                        "provider_retry_exhausted",
                        retryable=True,
                        status_code=response.status_code,
                    )
                delay = self._retry_delay(response, attempt)
                await _close_response(response)
                await self._sleep(delay)
                continue
            return response
        raise ProviderTransportError("provider_retry_exhausted", retryable=True)

    def _ensure_allowed(self, url: str, allowed_hosts: frozenset[str] | None) -> None:
        if allowed_hosts is None:
            return
        hostname = urlsplit(url).hostname
        if hostname is None or _normalized_host(hostname) not in allowed_hosts:
            raise ProviderTransportError("provider_endpoint_not_allowed")

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        retry_after = _retry_after_seconds(
            response.headers.get("Retry-After") if response else None
        )
        if retry_after is not None:
            return min(retry_after, self._retry_after_max_seconds)
        base_delay = (0.25, 1.0)[min(attempt, 1)]
        return base_delay * max(0.0, min(1.0, self._random()))


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(
                0.0,
                (parsedate_to_datetime(value).timestamp() - __import__("time").time()),
            )
        except (TypeError, ValueError, OverflowError):
            return None
