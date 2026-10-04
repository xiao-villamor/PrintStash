"""Single orchestration seam for mesh thumbnail generation.

The engine owns strategy selection and resource cleanup. Persistence remains a
separate concern because thumbnails are retryable derivatives of committed
Artifacts.
"""

from __future__ import annotations

import os
import resource
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from printstash_core.mesh.measurements import (
    InvalidMeshMeasurements,
    VolumeMeasurement,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    validate_geometry_extents,
    volume_value,
)
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media import (
    mesh_loading,
    mesh_measurements,
    mesh_policy,
    mesh_previews,
    mesh_render,
    mesh_resources,
    stl_fallback,
    stl_streaming,
)
from app.modules.media.fingerprints import (
    FingerprintResult,
    FingerprintResultState,
    extract,
)
from app.modules.media.mesh_contracts import (
    Geometry,
    GeometryNotLoaded,
    GeometryNotRequested,
    GeometryOutcome,
    GeometryReady,
    GeometryRefused,
    GeometryRepresentation,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailMetricsSink,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)
from app.modules.media.mesh_protocol import (
    BasicOutput,
    GeometryOutput,
    OutputSink,
    ThumbnailOutput,
)
from app.modules.media.mesh_resources import (
    DetachedSceneMesh,
    ExpandedScene,
    PreparedMesh,
    PreparedScene,
    prepare_loaded_mesh,
)
from app.modules.media.mesh_telemetry import (
    MeshPhase,
    PhaseOutcome,
    PhaseRecorder,
)
from app.modules.media.scene_measurements import VolumeTopologyRequired, measure_scene
from app.modules.media.stl_reader import (
    InvalidSTL,
    STLMeasurements,
    STLReadFailure,
    scan_stl,
)
from app.modules.media.three_mf_scene import Unsupported3MFCapability, read_scene
from app.modules.media.worker_bootstrap import WORKER_MARKER

if TYPE_CHECKING:
    from trimesh import Trimesh

logger = get_logger(__name__)


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
    except OSError, ValueError:
        return None


def _prepare_sampled_stl(
    path: Path, *, triangle_cap: int
) -> tuple[PreparedMesh, SourceScanState] | None:
    """Keep sample buffers within one lifetime, including construction failures."""
    import numpy as np
    import trimesh

    sampled = stl_fallback.read_stl_sample(
        path, max_triangles=min(10_000, triangle_cap)
    )
    if not sampled.sampled_triangles:
        return None
    points = np.array(sampled.coordinates, dtype=np.float64).reshape((-1, 3))
    mesh = trimesh.Trimesh(
        vertices=points,
        faces=np.arange(len(points)).reshape((-1, 3)),
        process=False,
    )
    prepared = PreparedMesh(
        mesh,
        ExpandedScene((), ()),
        geometry=SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
    )
    return prepared, (
        SourceScanState.COMPLETE if sampled.source_complete else SourceScanState.PARTIAL
    )


class _OutputDeliveryError(Exception):
    """Keep publication failures separate from native resource refusals."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        super().__init__(str(error))


@dataclass
class ThumbnailEngine:
    metrics: ThumbnailMetricsSink = field(default_factory=NoopThumbnailMetrics)

    def generate(
        self, request: ThumbnailRequest, *, on_output: OutputSink | None = None
    ) -> ThumbnailResult:
        started = time.monotonic()
        phases = PhaseRecorder()
        try:
            source_bytes = request.path.stat().st_size
        except OSError:
            source_bytes = None
        width = int(request.width or settings.model_thumbnail_width)
        height = int(request.height or round(width * 3 / 4))
        suffix = mesh_policy.canonical_suffix(request.path, request.file_type)
        geometry = _empty_geometry()
        volume: VolumeMeasurement = VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
            if request.include_geometry
            else VolumeNotCalculatedCause.NOT_REQUESTED
        )
        geometry_outcome: GeometryOutcome = (
            GeometryRefused(ThumbnailFailureReason.INVALID_SOURCE)
            if request.include_geometry
            else GeometryNotRequested()
        )
        strategy = ThumbnailStrategy.NONE
        source_scan = SourceScanState.NOT_SCANNED
        geometry_representation: GeometryRepresentation = GeometryNotLoaded()
        preview_coverage = PreviewCoverage.NOT_PRODUCED
        failure: ThumbnailFailureReason | None = None
        image: bytes | None = None
        mesh: Trimesh | None = None
        prepared: PreparedMesh | None = None
        detached_prepared: DetachedSceneMesh | None = None
        source_scene: PreparedScene | None = None
        scene_cleanup_pending = False
        fingerprint_result: FingerprintResult | None = None
        sample_buffers_pending = False
        geometry_emitted = False
        thumbnail_emitted = False
        reload_mesh_for_fingerprint = False
        stl_measurements: STLMeasurements | None = None

        def coverage() -> MeshCoverage:
            return MeshCoverage(source_scan, geometry_representation, preview_coverage)

        def deliver(output: BasicOutput) -> None:
            if on_output is not None:
                try:
                    on_output(output)
                except Exception as exc:
                    raise _OutputDeliveryError(exc) from exc

        def emit_geometry() -> None:
            nonlocal geometry_emitted, geometry_outcome
            if not request.include_geometry or geometry_emitted:
                return
            if geometry["triangle_count"] is not None:
                geometry_outcome = GeometryReady()
            if isinstance(geometry_outcome, GeometryNotRequested):
                raise ValueError("requested geometry has no outcome")
            geometry_emitted = True
            if on_output is not None:
                deliver(
                    GeometryOutput(
                        dict(geometry),
                        geometry_outcome,
                        volume,
                        coverage(),
                        max(round((time.monotonic() - started) * 1000), 0),
                        _peak_rss_bytes(),
                    )
                )

        def emit_thumbnail() -> None:
            nonlocal thumbnail_emitted, failure
            if not request.include_thumbnail or thumbnail_emitted:
                return
            emit_geometry()
            if image is not None:
                failure = None
            thumbnail_emitted = True
            if on_output is not None:
                deliver(
                    ThumbnailOutput(
                        image,
                        strategy,
                        coverage(),
                        failure,
                        max(round((time.monotonic() - started) * 1000), 0),
                        _peak_rss_bytes(),
                    )
                )

        def report(label: str) -> None:
            if request.report is not None:
                request.report(label)

        if (
            type(request.triangle_cap) is not int
            or not 100 <= request.triangle_cap <= MAX_ANALYSIS_FACES
        ):
            raise ValueError("invalid_triangle_cap")
        phases.start(MeshPhase.ADMISSION, input_bytes=source_bytes)
        report("loading_mesh")
        if request.file_type is None:
            over_cap = mesh_policy.exceeds_cap(request.path)
        else:
            over_cap = mesh_policy.exceeds_cap(request.path, file_type=suffix)
        phases.finish()

        if over_cap and request.include_geometry:
            geometry_outcome = GeometryRefused(ThumbnailFailureReason.RESOURCE_LIMIT)

        embedded = None
        try:
            with mesh_policy.render_admission():
                if (
                    request.include_thumbnail
                    and suffix == ".3mf"
                    and (
                        not over_cap
                        or settings.use_embedded_3mf_preview_for_large_files
                    )
                ):
                    phases.start(MeshPhase.EMBEDDED, input_bytes=source_bytes)
                    embedded = mesh_previews.extract_embedded_3mf_thumbnail(
                        request.path,
                        validate_image=True,
                        file_type=suffix if request.file_type is not None else None,
                    )

                    phases.finish(
                        output_bytes=len(embedded) if embedded is not None else None
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
                    preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
                else:
                    if not over_cap:
                        phases.start(MeshPhase.LOAD, input_bytes=source_bytes)
                        if suffix == ".3mf":
                            # Retained scenes admit unique source buffers and
                            # placed counts without allocating a whole mesh.
                            # Render, topology and FP apply their own budgets
                            # before a consumer materializes placed geometry.
                            try:
                                source_scene = PreparedScene(
                                    read_scene(
                                        request.path, max_faces=MAX_ANALYSIS_FACES
                                    )
                                )
                            except GeometryError as exc:
                                if exc.code in {
                                    "archive_resource_limit",
                                    "resource_limit",
                                    "scene_resource_limit",
                                }:
                                    over_cap = True
                                refusal = (
                                    ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
                                    if isinstance(exc, Unsupported3MFCapability)
                                    else ThumbnailFailureReason.RESOURCE_LIMIT
                                    if over_cap
                                    else ThumbnailFailureReason.INVALID_SOURCE
                                )
                                failure = refusal
                                if request.include_geometry:
                                    geometry_outcome = GeometryRefused(refusal)
                                if request.include_fingerprint:
                                    fingerprint_result = FingerprintResult(
                                        FingerprintResultState.UNSUPPORTED
                                        if isinstance(exc, Unsupported3MFCapability)
                                        else FingerprintResultState.FAILED,
                                        failure_code=FingerprintFailureCode(exc.code),
                                    )
                        elif request.include_fingerprint and suffix in (
                            ".step",
                            ".stp",
                        ):
                            try:
                                mesh = mesh_loading.load_step_mesh(
                                    request.path, include_brep=True
                                )
                            except GeometryError as exc:
                                if request.include_geometry:
                                    geometry_outcome = GeometryRefused(
                                        ThumbnailFailureReason.UNSUPPORTED_FORMAT
                                        if exc.code == "step_unavailable"
                                        else ThumbnailFailureReason.TIMEOUT
                                        if exc.code == "tessellation_timeout"
                                        else ThumbnailFailureReason.RESOURCE_LIMIT
                                        if exc.code
                                        in {"worker_oom", "geometry_work_limit"}
                                        else ThumbnailFailureReason.INVALID_SOURCE
                                    )
                                fingerprint_result = FingerprintResult(
                                    FingerprintResultState.UNSUPPORTED
                                    if exc.code == "step_unavailable"
                                    else FingerprintResultState.FAILED,
                                    failure_code=FingerprintFailureCode(exc.code),
                                )
                        else:
                            try:
                                mesh = (
                                    mesh_loading.load_mesh(request.path)
                                    if request.file_type is None
                                    else mesh_loading.load_mesh(
                                        request.path, file_type=suffix
                                    )
                                )
                            except GeometryError as exc:
                                over_cap = exc.code == "resource_limit"
                                refusal = (
                                    ThumbnailFailureReason.RESOURCE_LIMIT
                                    if over_cap
                                    else ThumbnailFailureReason.INVALID_SOURCE
                                )
                                failure = refusal
                                if request.include_geometry:
                                    geometry_outcome = GeometryRefused(refusal)
                                if request.include_fingerprint:
                                    fingerprint_result = FingerprintResult(
                                        FingerprintResultState.FAILED,
                                        failure_code=FingerprintFailureCode(exc.code),
                                    )

                        phases.finish(
                            outcome=PhaseOutcome.COMPLETED
                            if mesh is not None or source_scene is not None
                            else PhaseOutcome.FAILED,
                            triangle_count=source_scene.triangle_count
                            if source_scene is not None
                            else len(mesh.faces)
                            if mesh is not None
                            else None,
                        )

                    if source_scene is not None:
                        source_scan = SourceScanState.COMPLETE
                    elif mesh is not None:
                        source_scan = SourceScanState.COMPLETE
                        geometry_representation = CompleteGeometry()

                    report("extracting_geometry")
                    if request.include_geometry:
                        phases.start(MeshPhase.MEASUREMENTS)
                        if over_cap and suffix == ".stl":
                            try:
                                measured = scan_stl(request.path)
                            except (InvalidSTL, OSError) as exc:
                                geometry_outcome = GeometryRefused(
                                    ThumbnailFailureReason.RESOURCE_LIMIT
                                    if isinstance(exc, InvalidSTL)
                                    and exc.reason is STLReadFailure.RESOURCE_LIMIT
                                    else ThumbnailFailureReason.INVALID_SOURCE
                                )
                            else:
                                stl_measurements = measured
                                source_scan = SourceScanState.COMPLETE
                                volume = VolumeNotCalculated(
                                    VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                                )
                                geometry.update(
                                    {
                                        "bbox_x_mm": measured.bounds_max[0]
                                        - measured.bounds_min[0],
                                        "bbox_y_mm": measured.bounds_max[1]
                                        - measured.bounds_min[1],
                                        "bbox_z_mm": measured.bounds_max[2]
                                        - measured.bounds_min[2],
                                        "triangle_count": measured.triangle_count,
                                    }
                                )
                        elif source_scene is not None:
                            # Unique-resource topology caches also own native cycles.
                            # Release them before rendering, even without fingerprints.
                            scene_cleanup_pending = True
                            try:
                                scene_measurements = measure_scene(source_scene.scene)
                            except GeometryError as exc:
                                # Source syntax may be valid while placed coordinates
                                # exceed the numeric range. Embedded output is independent.
                                scene_cleanup_pending = True
                                source_scene = None
                                geometry = _empty_geometry()
                                volume = VolumeNotCalculated(
                                    VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
                                )
                                geometry_outcome = GeometryRefused(
                                    ThumbnailFailureReason.INVALID_SOURCE
                                )
                                if request.include_fingerprint:
                                    fingerprint_result = FingerprintResult(
                                        FingerprintResultState.FAILED,
                                        failure_code=FingerprintFailureCode(exc.code),
                                    )
                            else:
                                geometry = scene_measurements.geometry
                                scene_volume = scene_measurements.volume
                                if isinstance(scene_volume, VolumeTopologyRequired):
                                    volume = VolumeNotCalculated(
                                        VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                                    )
                                    if (
                                        source_scene.triangle_count
                                        <= mesh_policy.load_face_budget(suffix)
                                    ):
                                        scene_cleanup_pending = True
                                        try:
                                            prepared = mesh_resources.materialize_scene(
                                                source_scene.scene
                                            )
                                        except (GeometryError, MemoryError) as exc:
                                            # A failed topology attempt cannot withdraw
                                            # independent bounds/count or force an FP retry.
                                            if request.include_fingerprint:
                                                fingerprint_result = FingerprintResult(
                                                    FingerprintResultState.FAILED,
                                                    failure_code=FingerprintFailureCode(
                                                        exc.code
                                                    )
                                                    if isinstance(exc, GeometryError)
                                                    else FingerprintFailureCode.RESOURCE_LIMIT,
                                                )
                                        else:
                                            geometry_representation = CompleteGeometry()
                                            volume = (
                                                mesh_measurements.geometry_from_mesh(
                                                    prepared.whole_mesh
                                                ).volume
                                            )
                                            geometry["volume_mm3"] = volume_value(
                                                volume
                                            )
                                else:
                                    volume = scene_volume
                        else:
                            measured = mesh_measurements.geometry_from_mesh(mesh)
                            geometry, volume = measured.geometry, measured.volume
                        validate_geometry_extents(geometry)
                        phases.finish(
                            triangle_count=int(geometry["triangle_count"])
                            if geometry["triangle_count"] is not None
                            else None,
                            outcome=PhaseOutcome.COMPLETED
                            if geometry["triangle_count"] is not None
                            else PhaseOutcome.FAILED,
                        )
                        if geometry["triangle_count"] is not None:
                            geometry_outcome = GeometryReady()
                        elif over_cap and (
                            suffix != ".stl" or request.include_thumbnail
                        ):
                            geometry_outcome = GeometryRefused(
                                ThumbnailFailureReason.RESOURCE_LIMIT
                            )

                    emit_geometry()
                    if request.include_thumbnail:
                        phases.start(MeshPhase.RENDER)
                        report("rendering_thumbnail")
                        if embedded is not None:
                            image = embedded
                            strategy = ThumbnailStrategy.EMBEDDED
                            preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
                        elif mesh is not None or source_scene is not None:
                            if source_scene is not None:
                                # Keep only already-admitted placed arrays for later
                                # analysis; native mesh objects and topology caches
                                # are released before retained-scene rendering.
                                if (
                                    prepared is not None
                                    and request.include_fingerprint
                                    and fingerprint_result is None
                                    and len(prepared.whole_mesh.faces)
                                    <= request.triangle_cap
                                ):
                                    detached_prepared = (
                                        mesh_resources.detach_scene_mesh(
                                            prepared, triangle_cap=request.triangle_cap
                                        )
                                    )
                                prepared = None
                                mesh = None
                                if scene_cleanup_pending:
                                    scene_cleanup_pending = False
                                    mesh_policy.reclaim_memory()
                            if source_scene is not None:
                                render_triangles = source_scene.triangle_count
                            else:
                                assert mesh is not None
                                render_triangles = len(mesh.faces)
                            cap = int(settings.mesh_max_render_triangles)
                            ram_cap = mesh_policy.ram_triangle_cap(suffix)
                            if ram_cap is not None:
                                cap = min(cap, ram_cap)
                            if render_triangles > cap:
                                failure = ThumbnailFailureReason.RESOURCE_LIMIT
                                logger.warning(
                                    "thumbnail_engine: post-load triangle budget exceeded",
                                    extra={
                                        "strategy": "full",
                                        "triangles": render_triangles,
                                    },
                                )
                            else:
                                try:
                                    image = (
                                        mesh_render.render_scene_thumbnail(
                                            source_scene.scene,
                                            request.path.name,
                                            width=width,
                                            height=height,
                                            output_format=request.output_format,
                                        )
                                        if source_scene is not None
                                        else mesh_render.render_mesh_thumbnail(
                                            mesh,
                                            request.path.name,
                                            width=width,
                                            height=height,
                                            output_format=request.output_format,
                                        )
                                    )
                                except Exception:  # noqa: BLE001 - bounded fallbacks remain
                                    logger.exception(
                                        "thumbnail_engine: full renderer failed",
                                        extra={"format": suffix},
                                    )
                                if image is not None:
                                    strategy = ThumbnailStrategy.FULL
                                    preview_coverage = PreviewCoverage.COMPLETE

                        phases.finish(
                            output_bytes=len(image) if image is not None else None,
                            outcome=PhaseOutcome.COMPLETED
                            if image is not None
                            else PhaseOutcome.FAILED,
                        )

                        if (
                            image is None
                            and suffix == ".stl"
                            and (over_cap or mesh is not None)
                        ):
                            release_buffers = mesh is not None or sample_buffers_pending
                            reload_mesh_for_fingerprint = mesh is not None
                            prepared = None
                            mesh = None
                            sample_buffers_pending = False
                            if release_buffers:
                                mesh_policy.reclaim_memory()
                            phases.start(MeshPhase.STREAMING, input_bytes=source_bytes)
                            streamed = (
                                stl_streaming.render_stl_preview_isolated(
                                    request.path,
                                    width=width,
                                    height=height,
                                    measurements=stl_measurements,
                                )
                                if stl_measurements is not None
                                and os.environ.get(WORKER_MARKER) == str(os.getpid())
                                else stl_streaming.render_stl_preview_isolated(
                                    request.path, width=width, height=height
                                )
                            )
                            phases.finish(
                                output_bytes=len(streamed.png)
                                if streamed is not None
                                else None,
                                triangle_count=streamed.triangle_count
                                if streamed is not None
                                else None,
                                outcome=PhaseOutcome.COMPLETED
                                if streamed is not None
                                else PhaseOutcome.FAILED,
                            )
                            if streamed is not None:
                                image = streamed.png
                                strategy = ThumbnailStrategy.STREAMING
                                source_scan = SourceScanState.COMPLETE
                                preview_coverage = PreviewCoverage.COMPLETE
                                if (
                                    request.include_geometry
                                    and not geometry_emitted
                                    and geometry["triangle_count"] is None
                                ):
                                    volume = VolumeNotCalculated(
                                        VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                                    )
                                    geometry.update(
                                        {
                                            "bbox_x_mm": streamed.bounds_max[0]
                                            - streamed.bounds_min[0],
                                            "bbox_y_mm": streamed.bounds_max[1]
                                            - streamed.bounds_min[1],
                                            "bbox_z_mm": streamed.bounds_max[2]
                                            - streamed.bounds_min[2],
                                            "triangle_count": streamed.triangle_count,
                                        }
                                    )

                        if image is None and suffix == ".stl":
                            phases.start(MeshPhase.FALLBACK, input_bytes=source_bytes)
                            fallback = stl_fallback.render_stl_thumbnail(
                                request.path, width=width, height=height
                            )
                            phases.finish(
                                output_bytes=len(fallback.png)
                                if fallback is not None
                                else None,
                                triangle_count=fallback.triangle_count
                                if fallback is not None
                                else None,
                                outcome=PhaseOutcome.COMPLETED
                                if fallback is not None
                                else PhaseOutcome.FAILED,
                            )
                            if fallback is not None:
                                image = fallback.png
                                strategy = ThumbnailStrategy.FALLBACK
                                preview_coverage = (
                                    PreviewCoverage.COMPLETE
                                    if fallback.complete
                                    else PreviewCoverage.PARTIAL
                                )
                                if fallback.source_complete:
                                    source_scan = SourceScanState.COMPLETE
                                elif source_scan is SourceScanState.NOT_SCANNED:
                                    source_scan = SourceScanState.PARTIAL
                                if (
                                    request.include_geometry
                                    and not geometry_emitted
                                    and fallback.source_complete
                                    and geometry["triangle_count"] is None
                                ):
                                    volume = VolumeNotCalculated(
                                        VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                                    )
                                    geometry.update(
                                        {
                                            "bbox_x_mm": fallback.bounds_max[0]
                                            - fallback.bounds_min[0],
                                            "bbox_y_mm": fallback.bounds_max[1]
                                            - fallback.bounds_min[1],
                                            "bbox_z_mm": fallback.bounds_max[2]
                                            - fallback.bounds_min[2],
                                            "triangle_count": fallback.triangle_count,
                                        }
                                    )

                        if image is None and failure is None:
                            failure = (
                                ThumbnailFailureReason.RESOURCE_LIMIT
                                if over_cap
                                else ThumbnailFailureReason.NO_GEOMETRY
                                if mesh is None and source_scene is None
                                else ThumbnailFailureReason.RENDERER_NO_OUTPUT
                            )
                    emit_thumbnail()

                    if request.include_fingerprint and fingerprint_result is None:
                        phases.start(MeshPhase.FINGERPRINT)
                        report("extracting_fingerprint")
                        try:
                            if reload_mesh_for_fingerprint and mesh is None:
                                mesh = mesh_loading.load_mesh(
                                    request.path, file_type=suffix
                                )
                            # Analysis admission cannot remove useful measurements
                            # or a preview from a mesh admitted by the load budget.
                            if (
                                source_scene is not None
                                and source_scene.triangle_count > request.triangle_cap
                                or mesh is not None
                                and len(mesh.faces) > request.triangle_cap
                            ):
                                raise GeometryError("geometry_work_limit")
                            if prepared is None and detached_prepared is not None:
                                prepared = mesh_resources.restore_scene_mesh(
                                    detached_prepared
                                )
                            if prepared is None and source_scene is not None:
                                scene_cleanup_pending = True
                                prepared = mesh_resources.materialize_scene(
                                    source_scene.scene
                                )
                                geometry_representation = CompleteGeometry()
                            if prepared is None and mesh is not None:
                                prepared = prepare_loaded_mesh(
                                    mesh, file_type=suffix.lstrip(".")
                                )
                            if prepared is None and suffix == ".stl" and over_cap:
                                sample_buffers_pending = True
                                sampled_preparation = _prepare_sampled_stl(
                                    request.path, triangle_cap=request.triangle_cap
                                )
                                if sampled_preparation is not None:
                                    prepared, sampled_scan = sampled_preparation
                                    del sampled_preparation
                                    if source_scan is not SourceScanState.COMPLETE:
                                        source_scan = sampled_scan
                            if (
                                prepared is not None
                                and len(prepared.whole_mesh.faces)
                                > request.triangle_cap
                            ):
                                raise GeometryError("geometry_work_limit")
                            if prepared is not None and isinstance(
                                geometry_representation, GeometryNotLoaded
                            ):
                                geometry_representation = prepared.geometry
                                if isinstance(prepared.geometry, CompleteGeometry):
                                    source_scan = SourceScanState.COMPLETE
                            fingerprint_result = (
                                extract(prepared)
                                if prepared is not None
                                else FingerprintResult(
                                    FingerprintResultState.UNSUPPORTED
                                    if suffix in (".step", ".stp")
                                    else FingerprintResultState.FAILED,
                                    failure_code=FingerprintFailureCode.STEP_UNAVAILABLE
                                    if suffix in (".step", ".stp")
                                    else FingerprintFailureCode.RESOURCE_LIMIT
                                    if over_cap
                                    else FingerprintFailureCode.INVALID_SOURCE,
                                )
                            )
                        except (
                            GeometryError,
                            InvalidSTL,
                            OSError,
                            ValueError,
                            MemoryError,
                        ) as exc:
                            fingerprint_result = FingerprintResult(
                                FingerprintResultState.FAILED,
                                failure_code=FingerprintFailureCode(exc.code)
                                if isinstance(exc, GeometryError)
                                else FingerprintFailureCode(exc.reason.value)
                                if isinstance(exc, InvalidSTL)
                                else FingerprintFailureCode.SOURCE_UNAVAILABLE
                                if isinstance(exc, OSError)
                                else FingerprintFailureCode.ANALYSIS_FAILED,
                            )

                        finally:
                            # Fingerprints contain serialized descriptors, never
                            # mesh buffers. Optional analysis runs after preview
                            # delivery and releases its preparation immediately.
                            prepared = None
                            detached_prepared = None

                        phases.finish(
                            outcome=PhaseOutcome.COMPLETED
                            if fingerprint_result.state is FingerprintResultState.READY
                            else PhaseOutcome.FAILED
                        )

        except _OutputDeliveryError as exc:
            raise exc.error from None
        except InvalidMeshMeasurements:
            phases.fail_active()
            if not geometry_emitted:
                geometry = _empty_geometry()
            if request.include_geometry and not geometry_emitted:
                geometry_outcome = GeometryRefused(
                    ThumbnailFailureReason.INVALID_SOURCE
                )
                volume = VolumeNotCalculated(
                    VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
                )
            if request.include_fingerprint:
                fingerprint_result = FingerprintResult(
                    FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode.INVALID_SOURCE,
                )
            if embedded is not None:
                image = embedded
                strategy = ThumbnailStrategy.EMBEDDED
                preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
            elif request.include_thumbnail:
                failure = ThumbnailFailureReason.INVALID_SOURCE
        except MemoryError:
            phases.fail_active()
            prepared = None
            detached_prepared = None
            mesh = None
            source_scene = None
            sample_buffers_pending = False
            scene_cleanup_pending = False
            mesh_policy.reclaim_memory()
            if request.include_geometry and not isinstance(
                geometry_outcome, GeometryReady
            ):
                geometry_outcome = GeometryRefused(
                    ThumbnailFailureReason.RESOURCE_LIMIT
                )
            if request.include_fingerprint:
                fingerprint_result = FingerprintResult(
                    FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode.RESOURCE_LIMIT,
                )
            if embedded is not None:
                image = embedded
                strategy = ThumbnailStrategy.EMBEDDED
                preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
            else:
                failure = ThumbnailFailureReason.RESOURCE_LIMIT
        finally:
            phases.fail_active()
            prepared = None
            detached_prepared = None
            release_buffers = (
                mesh is not None
                or source_scene is not None
                or sample_buffers_pending
                or scene_cleanup_pending
            )
            mesh = None
            source_scene = None
            if release_buffers:
                mesh_policy.reclaim_memory()

        try:
            emit_geometry()
            emit_thumbnail()
        except _OutputDeliveryError as exc:
            raise exc.error from None
        if request.include_geometry and geometry["triangle_count"] is not None:
            geometry_outcome = GeometryReady()
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
            geometry_outcome=geometry_outcome,
            volume=volume,
            strategy=strategy,
            coverage=MeshCoverage(
                source_scan, geometry_representation, preview_coverage
            ),
            failure_reason=failure,
            duration_ms=duration_ms,
            peak_rss_bytes=peak_rss,
            fingerprint_result=fingerprint_result,
            phase_stats=phases.snapshot(),
        )


__all__ = [
    "NoopThumbnailMetrics",
    "ThumbnailEngine",
]
