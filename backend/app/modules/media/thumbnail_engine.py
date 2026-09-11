"""Single orchestration seam for mesh thumbnail generation.

The engine owns strategy selection and resource cleanup. Persistence remains a
separate concern because thumbnails are retryable derivatives of committed
Artifacts.
"""

from __future__ import annotations

import resource
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional, Protocol

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media import mesh_render, stl_fallback, stl_streaming
from app.modules.media.fingerprints import FingerprintResult, extract
from app.modules.media.mesh_resources import (
    ExpandedScene,
    PreparedMesh,
    load_3mf,
    prepare_loaded_mesh,
)

logger = get_logger(__name__)

Geometry = Dict[str, Optional[float]]
ProgressReporter = Callable[[str], None]


class ThumbnailStrategy(str, Enum):
    NONE = "none"
    EMBEDDED = "embedded"
    FULL = "full"
    STREAMING = "streaming"
    FALLBACK = "fallback"


class ThumbnailFailureReason(str, Enum):
    INVALID_SOURCE = "invalid_source"
    UNSUPPORTED_FORMAT = "unsupported_format"
    NO_GEOMETRY = "no_geometry"
    RESOURCE_LIMIT = "resource_limit"
    TIMEOUT = "timeout"
    RENDERER_NO_OUTPUT = "renderer_no_output"
    STORAGE = "storage"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class ThumbnailRequest:
    path: Path
    file_type: str | None = None
    width: int | None = None
    height: int | None = None
    include_geometry: bool = True
    reason: str = "ingestion"
    report: ProgressReporter | None = None
    output_format: Literal["PNG", "WEBP"] = "PNG"
    include_fingerprint: bool = False
    triangle_cap: int = MAX_ANALYSIS_FACES
    include_thumbnail: bool = True


@dataclass(frozen=True)
class ThumbnailResult:
    image: bytes | None
    geometry: Geometry
    strategy: ThumbnailStrategy
    complete: bool
    failure_reason: ThumbnailFailureReason | None
    duration_ms: int
    peak_rss_bytes: int | None
    fingerprint_result: FingerprintResult | None = None


class ThumbnailMetricsSink(Protocol):
    def increment(self, name: str, *, labels: dict[str, str]) -> None: ...

    def observe(self, name: str, value: float, *, labels: dict[str, str]) -> None: ...


class NoopThumbnailMetrics:
    def increment(self, name: str, *, labels: dict[str, str]) -> None:
        del name, labels

    def observe(self, name: str, value: float, *, labels: dict[str, str]) -> None:
        del name, value, labels


def _empty_geometry() -> Geometry:
    return {
        "bbox_x_mm": None,
        "bbox_y_mm": None,
        "bbox_z_mm": None,
        "volume_mm3": None,
        "triangle_count": None,
    }


def _peak_rss_bytes() -> int | None:
    try:
        # Linux reports KiB; macOS reports bytes. The supported server images
        # are Linux, while the conservative branch keeps local development sane.
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * 1024 if value < 1 << 40 else value
    except (OSError, ValueError):
        return None


@dataclass
class ThumbnailEngine:
    metrics: ThumbnailMetricsSink = field(default_factory=NoopThumbnailMetrics)

    def generate(self, request: ThumbnailRequest) -> ThumbnailResult:
        # Lazy import prevents a module cycle: mesh_processing exposes the
        # backwards-compatible public entry points that delegate back here.
        from app.modules.media import mesh_processing

        started = time.monotonic()
        width = int(request.width or settings.model_thumbnail_width)
        height = int(request.height or round(width * 3 / 4))
        suffix = mesh_processing._canonical_suffix(request.path, request.file_type)
        geometry = _empty_geometry()
        strategy = ThumbnailStrategy.NONE
        complete = False
        failure: ThumbnailFailureReason | None = None
        image: bytes | None = None
        mesh: Any | None = None
        prepared: PreparedMesh | None = None
        fingerprint_result: FingerprintResult | None = None

        def report(label: str) -> None:
            if request.report is not None:
                request.report(label)

        if (
            type(request.triangle_cap) is not int
            or not 100 <= request.triangle_cap <= MAX_ANALYSIS_FACES
        ):
            raise ValueError("invalid_triangle_cap")
        report("loading_mesh")
        if request.file_type is None:
            over_cap = mesh_processing._exceeds_cap(request.path)
        else:
            over_cap = mesh_processing._exceeds_cap(request.path, file_type=suffix)
        if request.include_fingerprint and not over_cap:
            estimate = mesh_processing._estimate_triangle_count(
                request.path, file_type=suffix
            )
            over_cap = estimate is not None and estimate > request.triangle_cap

        try:
            with mesh_processing._render_semaphore():
                embedded = None
                if (
                    request.include_thumbnail
                    and suffix == ".3mf"
                    and (
                        not over_cap
                        or settings.use_embedded_3mf_preview_for_large_files
                    )
                ):
                    embedded = mesh_processing.extract_embedded_3mf_thumbnail(
                        request.path,
                        validate_image=True,
                        file_type=suffix if request.file_type is not None else None,
                    )

                # A thumbnail-only repair can return a validated embedded image
                # without parsing the mesh archive. Ingestion still loads safe
                # meshes once because it also needs exact geometry metadata.
                if (
                    embedded is not None
                    and not request.include_geometry
                    and not request.include_fingerprint
                ):
                    image = embedded
                    strategy = ThumbnailStrategy.EMBEDDED
                    complete = True
                else:
                    if not over_cap:
                        if request.include_fingerprint and suffix == ".3mf":
                            try:
                                prepared = load_3mf(request.path)
                                mesh = prepared.whole_mesh
                            except GeometryError as exc:
                                fingerprint_result = FingerprintResult(
                                    "failed", failure_code=exc.code
                                )
                        elif request.include_fingerprint and suffix in (
                            ".step",
                            ".stp",
                        ):
                            try:
                                mesh = mesh_processing._load_step_mesh_isolated(
                                    request.path, include_brep=True
                                )
                            except GeometryError as exc:
                                fingerprint_result = FingerprintResult(
                                    "unsupported"
                                    if exc.code == "step_unavailable"
                                    else "failed",
                                    failure_code=exc.code,
                                )
                        elif request.file_type is None:
                            mesh = mesh_processing._load_mesh(request.path)
                        else:
                            mesh = mesh_processing._load_mesh(
                                request.path, file_type=suffix
                            )

                    report("extracting_geometry")
                    if request.include_geometry:
                        geometry = mesh_processing._geometry_from_mesh(mesh)

                    if request.include_fingerprint and fingerprint_result is None:
                        report("extracting_fingerprint")
                        try:
                            if prepared is None and mesh is not None:
                                prepared = prepare_loaded_mesh(
                                    mesh, file_type=suffix.lstrip(".")
                                )
                            if prepared is None and suffix == ".stl" and over_cap:
                                import numpy as np
                                import trimesh

                                sampled = stl_fallback.sample_stl_geometry(
                                    request.path,
                                    max_triangles=min(10_000, request.triangle_cap),
                                )
                                if sampled is not None and sampled.sampled_triangles:
                                    points = np.array(
                                        sampled.coordinates, dtype=np.float64
                                    ).reshape((-1, 3))
                                    sampled_mesh = trimesh.Trimesh(
                                        vertices=points,
                                        faces=np.arange(len(points)).reshape((-1, 3)),
                                        process=False,
                                    )
                                    prepared = PreparedMesh(
                                        sampled_mesh,
                                        ExpandedScene((), ()),
                                        "stl",
                                        complete=False,
                                        failure_code="sampled_source",
                                    )
                            if (
                                prepared is not None
                                and len(prepared.whole_mesh.faces)
                                > request.triangle_cap
                            ):
                                raise GeometryError("geometry_work_limit")
                            fingerprint_result = (
                                extract(prepared)
                                if prepared is not None
                                else FingerprintResult(
                                    "unsupported"
                                    if suffix in (".step", ".stp")
                                    else "failed",
                                    failure_code="step_unavailable"
                                    if suffix in (".step", ".stp")
                                    else "resource_limit"
                                    if over_cap
                                    else "invalid_source",
                                )
                            )
                        except (GeometryError, ValueError, MemoryError) as exc:
                            fingerprint_result = FingerprintResult(
                                "failed",
                                failure_code=exc.code
                                if isinstance(exc, GeometryError)
                                else "analysis_failed",
                            )

                    if not request.include_thumbnail:
                        return ThumbnailResult(
                            image=None,
                            geometry=geometry,
                            strategy=ThumbnailStrategy.NONE,
                            complete=prepared.complete
                            if prepared
                            else mesh is not None,
                            failure_reason=None,
                            duration_ms=max(
                                round((time.monotonic() - started) * 1000), 0
                            ),
                            peak_rss_bytes=_peak_rss_bytes(),
                            fingerprint_result=fingerprint_result,
                        )

                    report("rendering_thumbnail")
                    if embedded is not None:
                        image = embedded
                        strategy = ThumbnailStrategy.EMBEDDED
                        complete = True
                    elif mesh is not None:
                        cap = int(settings.mesh_max_render_triangles)
                        ram_cap = mesh_processing._ram_triangle_cap(suffix)
                        if ram_cap is not None:
                            cap = min(cap, ram_cap)
                        if len(mesh.faces) > cap:
                            failure = ThumbnailFailureReason.RESOURCE_LIMIT
                            logger.warning(
                                "thumbnail_engine: post-load triangle budget exceeded",
                                extra={
                                    "strategy": "full",
                                    "triangles": len(mesh.faces),
                                },
                            )
                        else:
                            try:
                                image = mesh_render.render_mesh_thumbnail(
                                    mesh,
                                    request.path.name,
                                    width=width,
                                    height=height,
                                    output_format=request.output_format,
                                )
                            except Exception:  # noqa: BLE001 - bounded fallbacks remain
                                logger.exception(
                                    "thumbnail_engine: full renderer failed",
                                    extra={"format": suffix},
                                )
                            if image is not None:
                                strategy = ThumbnailStrategy.FULL
                                complete = True

                    if (
                        image is None
                        and suffix == ".stl"
                        and (over_cap or mesh is not None)
                    ):
                        if mesh is not None:
                            del mesh
                            mesh = None
                            mesh_processing._reclaim_memory()
                        streamed = stl_streaming.render_stl_preview_isolated(
                            request.path, width=width, height=height
                        )
                        if streamed is not None:
                            image = streamed.png
                            strategy = ThumbnailStrategy.STREAMING
                            complete = True
                            if geometry["triangle_count"] is None:
                                geometry.update(
                                    {
                                        "bbox_x_mm": round(
                                            streamed.bounds_max[0]
                                            - streamed.bounds_min[0],
                                            2,
                                        ),
                                        "bbox_y_mm": round(
                                            streamed.bounds_max[1]
                                            - streamed.bounds_min[1],
                                            2,
                                        ),
                                        "bbox_z_mm": round(
                                            streamed.bounds_max[2]
                                            - streamed.bounds_min[2],
                                            2,
                                        ),
                                        "triangle_count": streamed.triangle_count,
                                    }
                                )

                    if image is None and suffix == ".stl":
                        fallback = stl_fallback.render_stl_thumbnail(
                            request.path, width=width, height=height
                        )
                        if fallback is not None:
                            image = fallback.png
                            strategy = ThumbnailStrategy.FALLBACK
                            complete = fallback.complete
                            if fallback.complete and geometry["triangle_count"] is None:
                                geometry.update(
                                    {
                                        "bbox_x_mm": round(
                                            fallback.bounds_max[0]
                                            - fallback.bounds_min[0],
                                            2,
                                        ),
                                        "bbox_y_mm": round(
                                            fallback.bounds_max[1]
                                            - fallback.bounds_min[1],
                                            2,
                                        ),
                                        "bbox_z_mm": round(
                                            fallback.bounds_max[2]
                                            - fallback.bounds_min[2],
                                            2,
                                        ),
                                        "triangle_count": fallback.triangle_count,
                                    }
                                )

                    if image is None and failure is None:
                        failure = (
                            ThumbnailFailureReason.RESOURCE_LIMIT
                            if over_cap
                            else ThumbnailFailureReason.NO_GEOMETRY
                            if mesh is None
                            else ThumbnailFailureReason.RENDERER_NO_OUTPUT
                        )
        finally:
            prepared = None
            if mesh is not None:
                del mesh
                mesh_processing._reclaim_memory()

        if image is not None:
            failure = None
        duration_ms = max(round((time.monotonic() - started) * 1000), 0)
        labels = {
            "format": suffix.removeprefix(".") or "unknown",
            "strategy": strategy.value,
            "outcome": "generated" if image is not None else "failed",
            "reason": failure.value if failure is not None else "none",
        }
        self.metrics.increment("thumbnail_generation_total", labels=labels)
        self.metrics.observe(
            "thumbnail_generation_duration_ms", float(duration_ms), labels=labels
        )
        try:
            self.metrics.observe(
                "thumbnail_input_bytes",
                float(request.path.stat().st_size),
                labels=labels,
            )
        except OSError:
            pass
        if image is not None:
            self.metrics.observe(
                "thumbnail_renderer_output_bytes", float(len(image)), labels=labels
            )
        triangles = geometry.get("triangle_count")
        if triangles is not None:
            self.metrics.observe("thumbnail_triangles", float(triangles), labels=labels)
        peak_rss = _peak_rss_bytes()
        if peak_rss is not None:
            self.metrics.observe(
                "thumbnail_peak_rss_bytes", float(peak_rss), labels=labels
            )
        return ThumbnailResult(
            image=image,
            geometry=geometry,
            strategy=strategy,
            complete=complete,
            failure_reason=failure,
            duration_ms=duration_ms,
            peak_rss_bytes=peak_rss,
            fingerprint_result=fingerprint_result,
        )


__all__ = [
    "NoopThumbnailMetrics",
    "ThumbnailEngine",
    "ThumbnailFailureReason",
    "ThumbnailMetricsSink",
    "ThumbnailRequest",
    "ThumbnailResult",
    "ThumbnailStrategy",
]
