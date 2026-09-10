"""Bounded native float32 cosine scoring, with deterministic tie ordering."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import islice
from typing import Iterable

from .embedding import EmbeddingError


@dataclass(frozen=True)
class VectorEntry:
    unit_id: int
    subject_id: int
    blob: bytes


@dataclass(frozen=True)
class Neighbor:
    unit_id: int
    subject_id: int
    score: float


@dataclass(frozen=True)
class NeighborResult:
    items: tuple[Neighbor, ...]
    scanned: int
    truncated: bool


def normalize(vector: Iterable[float], dimension: int) -> bytes:
    import numpy as np

    if not 1 <= dimension <= 4096:
        raise EmbeddingError("embedding_dimension_invalid")
    values = np.asarray(tuple(islice(vector, dimension + 1)), dtype=np.float64)
    if values.shape != (dimension,) or not np.isfinite(values).all():
        raise EmbeddingError("embedding_vector_invalid")
    with np.errstate(over="ignore", invalid="ignore"):
        norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or norm < 1e-12:
        raise EmbeddingError("embedding_vector_invalid")
    return (values / norm).astype("<f4").tobytes()


def cosine_neighbors(
    query: bytes,
    entries: Iterable[VectorEntry],
    *,
    dimension: int,
    limit: int = 20,
    max_scan: int = 100_000,
    block_size: int = 256,
) -> NeighborResult:
    """Aggregate by Subject, never retaining more than `limit` Subjects.

    Input iteration is lazy and capped; no NxN matrix and no corpus-sized array.
    Callers supply only authorized, current units in one immutable Space.
    """
    import numpy as np

    if not (
        1 <= dimension <= 4096
        and 1 <= limit <= 100
        and 1 <= block_size <= 512
        and 1 <= max_scan <= 1_000_000
    ):
        raise EmbeddingError("embedding_query_budget_invalid")
    if len(query) != 4 * dimension:
        raise EmbeddingError("embedding_dimension_mismatch")
    query_values = np.frombuffer(
        normalize(np.frombuffer(query, dtype="<f4"), dimension), dtype="<f4"
    )
    best: dict[int, Neighbor] = {}
    scanned = 0
    iterator = iter(entries)
    while scanned < max_scan:
        block = tuple(islice(iterator, min(block_size, max_scan - scanned)))
        if not block:
            break
        if any(len(entry.blob) != 4 * dimension for entry in block):
            raise EmbeddingError("embedding_dimension_mismatch")
        matrix = np.stack([np.frombuffer(entry.blob, dtype="<f4") for entry in block])
        norms = np.linalg.norm(matrix, axis=1)
        if (
            not np.isfinite(matrix).all()
            or not np.isfinite(norms).all()
            or np.any(norms < 1e-12)
        ):
            raise EmbeddingError("embedding_vector_invalid")
        scores = np.clip((matrix / norms[:, None]) @ query_values, -1.0, 1.0)
        for entry, score in zip(block, scores, strict=True):
            neighbor = Neighbor(entry.unit_id, entry.subject_id, float(score))
            previous = best.get(entry.subject_id)
            if previous is None or (neighbor.score, -neighbor.unit_id) > (
                previous.score,
                -previous.unit_id,
            ):
                best[entry.subject_id] = neighbor
            if len(best) > limit:
                worst = min(
                    best.values(),
                    key=lambda item: (item.score, -item.subject_id, -item.unit_id),
                )
                del best[worst.subject_id]
        scanned += len(block)
    # One-row lookahead makes budget truncation explicit to the caller.
    truncated = next(iterator, None) is not None
    items = heapq.nsmallest(
        limit,
        best.values(),
        key=lambda item: (-item.score, item.subject_id, item.unit_id),
    )
    return NeighborResult(tuple(items), scanned, truncated)
