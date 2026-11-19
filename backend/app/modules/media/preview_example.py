"""Cached rendering of a fixed, small bracket for the appearance controls."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock

from printstash_core.mesh.preview_profile import (
    PreviewAppearance,
    appearance_scope,
)

from .mesh_render import render_example_bracket

_sample_lock = Lock()


@lru_cache(maxsize=10)
def _sample(appearance: PreviewAppearance, fingerprint: str) -> bytes:
    with appearance_scope(appearance):
        image = render_example_bracket()
    if image is None:
        raise RuntimeError("preview_example_unavailable")
    return image


def render_example(appearance: PreviewAppearance) -> bytes:
    # Cache cold requests serially so concurrent readers cannot duplicate native work.
    with _sample_lock:
        return _sample(appearance, appearance.fingerprint)
