"""Resolved scalar configuration for isolated native workers.

The application keeps its validated ConfigResolver. Disposable mesh workers
receive only this explicit projection, before loading any media modules, so
starting a parser does not rebuild the web application's settings schema.
No credentials, storage paths, engine state or defaults cross this boundary.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any

_NAMES = frozenset(
    (
        "compute_adapter",
        "compute_backend",
        "compute_batch_wait_ms",
        "compute_memory_mb",
        "compute_mode",
        "compute_render_policy",
        "embedding_memory_budget_fraction",
        "embedding_resident_workers",
        "embedding_worker_memory_mb",
        "log_level",
        "max_render_jobs",
        "mesh_max_load_mb",
        "mesh_max_render_triangles",
        "mesh_memory_budget_fraction",
        "mesh_prepared_max_jobs",
        "mesh_prepared_max_mb",
        "mesh_render_face_chunk_size",
        "mesh_source_io_jobs",
        "mesh_step_timeout_seconds",
        "mesh_worker_template_enabled",
        "mesh_worker_timeout_seconds",
        "model_thumbnail_width",
        "three_mf_preview_max_archive_mb",
        "three_mf_preview_max_central_directory_mb",
        "three_mf_preview_max_concurrent",
        "three_mf_preview_max_entries",
        "three_mf_preview_max_ratio",
        "three_mf_preview_max_uncompressed_mb",
        "use_embedded_3mf_preview_for_large_files",
    )
)
_snapshot: MappingProxyType[str, str | int | float | bool | None] | None = None


def installed() -> bool:
    return _snapshot is not None


def capture() -> dict[str, str | int | float | bool | None]:
    """Capture effective values together; the parent already validated settings."""
    if _snapshot is not None:
        return dict(_snapshot)
    from app.core.config import _overlay
    from app.core.config import settings as application_settings

    overlay = dict(_overlay)
    return {
        name: overlay[name]
        if overlay.get(name) is not None
        else getattr(application_settings.frozen, name)
        for name in _NAMES
    }


def install(values: object) -> None:
    """Adopt one complete projection inside a newly isolated process."""
    global _snapshot
    if _snapshot is not None:
        raise RuntimeError("native settings already installed")
    if not isinstance(values, dict) or set(values) != _NAMES:
        raise ValueError("invalid native settings fields")
    if any(
        type(value) not in (str, int, float, bool, type(None))
        or isinstance(value, float)
        and not math.isfinite(value)
        for value in values.values()
    ):
        raise ValueError("invalid native setting value")
    _snapshot = MappingProxyType(dict(values))


class _NativeSettings:
    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("native settings are read-only")

    def __getattr__(self, name: str) -> Any:
        if _snapshot is not None:
            try:
                return _snapshot[name]
            except KeyError:
                raise AttributeError(name) from None
        from app.core.config import settings as application_settings

        return getattr(application_settings, name)


settings = _NativeSettings()
