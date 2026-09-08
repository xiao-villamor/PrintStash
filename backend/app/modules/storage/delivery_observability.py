"""Bounded delivery telemetry; object locators and bearer URLs never enter labels."""

from __future__ import annotations

from collections.abc import Iterator

from prometheus_client import Counter

from app.core.metrics import registry

_PROVIDERS = frozenset(
    {"local", "s3", "r2", "b2", "wasabi", "webdav", "sftp", "opendal", "external"}
)
_PURPOSES = frozenset(
    {"download", "browser_fetch", "thumbnail", "slicer", "public_share", "transformed"}
)
_STRATEGIES = frozenset({"redirect", "local", "proxy", "not_modified", "rejected"})
_strategies = Counter(
    "printstash_delivery_strategy_total",
    "Authorized delivery strategies.",
    ("provider", "purpose", "strategy"),
    registry=registry,
)
_bytes = Counter(
    "printstash_delivery_proxied_bytes_total",
    "Bytes consumed from API proxy delivery streams.",
    ("provider", "purpose"),
    registry=registry,
)


def record_strategy(provider: str, purpose: str, strategy: str) -> None:
    try:
        _strategies.labels(
            provider if provider in _PROVIDERS else "unknown",
            purpose if purpose in _PURPOSES else "unknown",
            strategy if strategy in _STRATEGIES else "unknown",
        ).inc()
    except Exception:
        pass  # Telemetry must never alter delivery or lease lifetime.


class MeteredChunks(Iterator[bytes]):
    def __init__(self, chunks: Iterator[bytes], provider: str, purpose: str):
        self._chunks = chunks
        self._provider = provider if provider in _PROVIDERS else "unknown"
        self._purpose = purpose if purpose in _PURPOSES else "unknown"
        self._closed = False

    def __iter__(self) -> MeteredChunks:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            chunk = next(self._chunks)
        except BaseException:
            self.close()
            raise
        try:
            _bytes.labels(self._provider, self._purpose).inc(len(chunk))
        except Exception:
            pass
        return chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            closer = getattr(self._chunks, "close", None)
            if closer is not None:
                closer()
