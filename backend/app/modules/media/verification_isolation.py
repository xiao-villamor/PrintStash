"""Pairwise geometric verification in a disposable worker process.

Verification loads two meshes and compares them, the heaviest single step of a
similarity run, and it runs over meshes chosen by the library's contents rather
than by any request. In the API process an out-of-memory kill takes every request
with it (#259). Here the parent supervises a child exactly as it does for mesh
derivatives (`mesh_isolation`): memory and time are bounded, and a child that
exceeds either costs one pair.

`verify_paths` has the signature of `geometry_analysis.verify_paths` and is a
drop-in for it. Expected geometry failures come back as the same `GeometryError`
codes, so callers count them as before; any other outcome is a `MeshWorkerError`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.mesh.similarity.verification import Verification

from app.modules.media import mesh_isolation
from app.modules.media.geometry_analysis import MAX_VERIFICATION_SECONDS
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import (
    MeshWorkerError,
    pack_value,
    raise_reported_error,
    unpack_value,
)
from app.modules.media.native_budget import MeshSource

EVIDENCE_MAGIC = b"VRF1"


def encode_reply(evidence: Verification) -> bytes:
    body = json.dumps(pack_value(dataclasses.asdict(evidence)), allow_nan=False)
    return EVIDENCE_MAGIC + body.encode()


def decode_reply(payload: bytes) -> Verification:
    """Rebuild the evidence, or raise the failure the worker reported."""
    raise_reported_error(payload)
    try:
        if not payload.startswith(EVIDENCE_MAGIC):
            raise ValueError("magic")
        fields = unpack_value(json.loads(payload[len(EVIDENCE_MAGIC) :]))
        return Verification(**fields)
    except (ValueError, TypeError, KeyError) as exc:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc


def verify_paths(
    first: Path,
    second: Path,
    *,
    first_type: str,
    second_type: str,
    first_component: int = 0,
    second_component: int = 0,
    sample_points: int = 5000,
    triangle_cap: int = MAX_ANALYSIS_FACES,
    verification_seconds: float = MAX_VERIFICATION_SECONDS,
) -> Verification:
    """`geometry_analysis.verify_paths`, run in a supervised child."""
    spec = {
        "first": mesh_isolation.absolute(first),
        "second": mesh_isolation.absolute(second),
        "first_type": first_type,
        "second_type": second_type,
        "first_component": first_component,
        "second_component": second_component,
        "sample_points": sample_points,
        "triangle_cap": triangle_cap,
        "verification_seconds": verification_seconds,
    }
    return decode_reply(
        mesh_isolation.run_worker(
            "app.modules.media.verification_worker",
            spec,
            sources=(MeshSource(first, first_type), MeshSource(second, second_type)),
        )
    )
