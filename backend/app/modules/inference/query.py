"""Bounded interactive inference and an ephemeral vector-only LRU cache."""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.vectors import normalize


class QueryRunner:
    """No queue beyond the worker count; no input text survives a completed call."""

    def __init__(self, *, workers: int = 2, cache_entries: int = 256, ttl: float = 300):
        if (
            not 1 <= workers <= 4
            or not 1 <= cache_entries <= 1024
            or not 0 < ttl <= 3600
        ):
            raise ValueError("search_query_budget_invalid")
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="inference-query"
        )
        self._slots = threading.BoundedSemaphore(workers)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._active: set[threading.Event] = set()
        self._closed = False
        self._entries, self._ttl = cache_entries, ttl

    def embed(
        self,
        provider,
        space: EmbeddingSpace,
        value: EmbeddingInput,
        *,
        authorization: str,
        seconds: float,
    ) -> tuple[float, ...]:
        if value.modality == "image" and space.modality not in {
            "image",
            "text_image",
            "point_cloud",
        }:
            raise EmbeddingError("embedding_image_unavailable")
        # Image queries are ephemeral even at the vector-cache layer. The same
        # executor, concurrency limit and cancellation contract serve both modes.
        key = (
            hashlib.sha256(
                json.dumps(
                    [space.config_hash, authorization, value.text],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if value.modality == "text"
            else None
        )
        cancelled = threading.Event()
        context = InferenceContext.bounded(seconds, cancelled=cancelled.is_set)
        with self._lock:
            if self._closed:
                raise EmbeddingError("inference_query_unavailable")
            cached = self._cache.pop(key, None) if key is not None else None
            if key is not None and cached and cached[0] > time.monotonic():
                self._cache[key] = cached
                return struct.unpack(f"<{space.dimension}f", cached[1])
            prepare = getattr(provider, "prepare_query", None)
            if prepare is not None:
                prepare()
            if not self._slots.acquire(blocking=False):
                raise EmbeddingError("inference_query_busy")
            self._active.add(cancelled)
            try:
                future = self._executor.submit(
                    copy_context().run, provider.embed, (value,), space, context=context
                )
            except RuntimeError:
                self._active.discard(cancelled)
                self._slots.release()
                raise EmbeddingError("inference_query_unavailable") from None

        def finished(_future):
            with self._lock:
                self._active.discard(cancelled)
            self._slots.release()

        future.add_done_callback(finished)
        try:
            vectors = future.result(timeout=context.remaining())
            context.remaining()
            if len(vectors) != 1:
                raise EmbeddingError("embedding_response_invalid")
            blob = normalize(vectors[0], space.dimension)
        except TimeoutError:
            raise EmbeddingError("inference_timeout") from None
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError("inference_query_unavailable") from None
        finally:
            cancelled.set()
        with self._lock:
            if not self._closed and key is not None:
                self._cache[key] = (time.monotonic() + self._ttl, blob)
                while len(self._cache) > self._entries:
                    self._cache.popitem(last=False)
        return struct.unpack(f"<{space.dimension}f", blob)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cache.clear()
            for cancelled in self._active:
                cancelled.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


_runner: QueryRunner | None = None
_runner_lock = threading.Lock()


def runner() -> QueryRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = QueryRunner()
        return _runner


def close_queries() -> None:
    global _runner
    with _runner_lock:
        previous, _runner = _runner, None
    if previous is not None:
        previous.close()
