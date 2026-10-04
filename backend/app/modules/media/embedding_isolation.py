"""Render the views a similarity embedding is computed from in a disposable worker.

The embedding pass loads and rasterises six views of every model in the library,
so it is heavy work over files the library chose rather than any request. In the
API process an out-of-memory kill takes every request with it (#259). The parent
supervises a child exactly as it does for mesh derivatives (`mesh_isolation`), so a
model that exhausts memory or time costs one embedding unit, not the API.

`embedding_views` has the signature of `geometry_analysis.embedding_views` and is a
drop-in for it. Expected geometry failures come back as the same `GeometryError`
codes, so callers count them as before; any other outcome is a `MeshWorkerError`.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

from printstash_core.inference import EmbeddingInput

from app.modules.media import mesh_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import (
    MeshWorkerError,
    raise_reported_error,
)
from app.modules.media.mesh_wire_values import pack_value, unpack_value
from app.modules.media.native_budget import MeshSource

VIEWS_MAGIC = b"EMB1"
# Six canonical frames; a worker that returns more is not the worker we started.
MAX_VIEWS = 8


def encode_reply(views: Sequence[EmbeddingInput]) -> bytes:
    body = json.dumps(
        pack_value([dataclasses.asdict(view) for view in views]), allow_nan=False
    )
    return VIEWS_MAGIC + body.encode()


def decode_reply(payload: bytes) -> tuple[EmbeddingInput, ...]:
    """Rebuild the views, or raise the failure the worker reported.

    `EmbeddingInput` validates its own bounds (modality, dimensions, byte counts),
    so a frame that violates them is refused by the type the parent already trusts.
    """
    raise_reported_error(payload)
    try:
        if not payload.startswith(VIEWS_MAGIC):
            raise ValueError("magic")
        items = unpack_value(json.loads(payload[len(VIEWS_MAGIC) :]))
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_VIEWS:
            raise ValueError("views")
        views = tuple(EmbeddingInput(**item) for item in items)
        if any(view.modality != "image" for view in views):
            raise ValueError("modality")
        return views
    except (ValueError, TypeError, KeyError) as exc:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc


def embedding_views(
    path: Path,
    *,
    file_type: str,
    component_index: int,
    image_size: int,
    triangle_cap: int,
) -> tuple[EmbeddingInput, ...]:
    """`geometry_analysis.embedding_views`, run in a supervised child."""
    spec = {
        "path": mesh_isolation.absolute(path),
        "file_type": file_type,
        "component_index": component_index,
        "image_size": image_size,
        "triangle_cap": triangle_cap,
    }
    return decode_reply(
        mesh_isolation.run_worker(
            "app.modules.media.embedding_worker",
            spec,
            sources=(MeshSource(path, file_type),),
        )
    )
