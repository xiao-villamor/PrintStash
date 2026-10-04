"""Run the mesh analysis pipeline the way the mesh derivative producer does.

One mesh load extracts geometry and renders the thumbnail; tests drive that
pipeline (``ThumbnailEngine``) directly and assert on its result: the image,
the geometry, and which strategy produced them (a streamed or fallback render
has explicit source scan, geometry representation and preview coverage facts).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.media.mesh_contracts import (
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.thumbnail_engine import ThumbnailEngine

PARTIAL_STRATEGIES = (ThumbnailStrategy.STREAMING, ThumbnailStrategy.FALLBACK)


def analyze(path: Path, **request: Any) -> ThumbnailResult:
    """Geometry and a thumbnail of ``path`` from one load."""
    return ThumbnailEngine().generate(ThumbnailRequest(path=path, **request))


def is_partial_render(result: ThumbnailResult) -> bool:
    """The image came from a streamed or fallback render, not the full mesh."""
    return result.strategy in PARTIAL_STRATEGIES
