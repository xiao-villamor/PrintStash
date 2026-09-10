"""Pure geometry evidence; never Model identity or permission to merge content."""

from .fingerprint import (
    ALGORITHM_VERSION,
    CanonicalKeys,
    D2Descriptor,
    FingerprintBudget,
    GeometryError,
    MeshFingerprint,
    SurfaceMetrics,
    fingerprint_mesh,
)

__all__ = [
    "ALGORITHM_VERSION",
    "CanonicalKeys",
    "D2Descriptor",
    "FingerprintBudget",
    "GeometryError",
    "MeshFingerprint",
    "SurfaceMetrics",
    "fingerprint_mesh",
]
