"""Refuse an owner started under a different immutable resource policy."""

import hashlib
import json

from app.core.config import settings

from .discovery import runtime_identity


def identity():
    value = {
        "mode": settings.compute_mode,
        "adapter": settings.compute_adapter,
        "backend": settings.compute_backend,
        "render_policy": settings.compute_render_policy,
        "device_memory_mb": settings.compute_memory_mb,
        "host_memory_mb": settings.embedding_worker_memory_mb,
        "resident_workers": settings.embedding_resident_workers,
        "host_fraction": settings.embedding_memory_budget_fraction,
        "geometry_fraction": settings.mesh_memory_budget_fraction,
        "batch_wait_ms": settings.compute_batch_wait_ms,
        "runtime": runtime_identity(),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
