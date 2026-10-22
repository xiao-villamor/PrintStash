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

from app.core.cancellation import OperationCancelled
from app.core.logging import get_logger
from app.core.native_settings import settings
from app.modules.media import (
    mesh_loading,
    mesh_measurements,
    mesh_policy,
    mesh_previews,
    mesh_render,
    mesh_resources,
    stl_fallback,
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
    PreparedThumbnailResult,
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
    PreparedThumbnailOutput,
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
from app.modules.media.stl_reader import (
    InvalidSTL,
    STLMeasurements,
    STLReadFailure,
    scan_stl,
)
from app.modules.media.three_mf_scene import (
    ThreeMFReader,
    Unsupported3MFCapability,
    read_scene_with_reader,
)
from app.modules.media.thumbnail_packet import PacketDescriptor, write_packet
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


class _Generation:
    """One run of the mesh pipeline over one source, as named stages.

    ``run`` is the whole of it: estimate, embedded, parse, measure, render,
    fingerprint, then finish. Each stage is one method and reads and writes
    the state of the run, which is everything a later stage (or the refusal
    handling around them) needs from an earlier one: what was loaded, what was
    measured, what has already been published. Anything a stage only needs
    for itself is a local of that stage.

    Outputs are published as soon as they exist (``emit_geometry``,
    ``emit_thumbnail``), each at most once, so a later stage that fails cannot
    take back an earlier one's result.
    """

    def __init__(
        self,
        metrics: ThumbnailMetricsSink,
        request: ThumbnailRequest,
        *,
        on_output: OutputSink | None,
        output_directory: Path | None,
        preparation_deadline: float | None,
    ) -> None:
        self.metrics = metrics
        self.request = request
        self.on_output = on_output
        self.output_directory = output_directory
        self.preparation_deadline = preparation_deadline
        self.started = time.monotonic()
        self.phases = PhaseRecorder()
        try:
            self.source_bytes = self.request.path.stat().st_size
        except OSError:
            self.source_bytes = None
        self.width = int(self.request.width or settings.model_thumbnail_width)
        self.height = int(self.request.height or round(self.width * 3 / 4))
        self.suffix = mesh_policy.canonical_suffix(
            self.request.path, self.request.file_type
        )
        self.geometry = _empty_geometry()
        self.volume: VolumeMeasurement = VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
            if self.request.include_geometry
            else VolumeNotCalculatedCause.NOT_REQUESTED
        )
        self.geometry_outcome: GeometryOutcome = (
            GeometryRefused(ThumbnailFailureReason.INVALID_SOURCE)
            if self.request.include_geometry
            else GeometryNotRequested()
        )
        self.strategy = ThumbnailStrategy.NONE
        self.source_scan = SourceScanState.NOT_SCANNED
        self.geometry_representation: GeometryRepresentation = GeometryNotLoaded()
        self.preview_coverage = PreviewCoverage.NOT_PRODUCED
        self.failure: ThumbnailFailureReason | None = None
        self.image: bytes | None = None
        self.packet: PacketDescriptor | None = None
        self.mesh: Trimesh | None = None
        self.prepared: PreparedMesh | None = None
        self.detached_prepared: DetachedSceneMesh | None = None
        self.source_scene: PreparedScene | None = None
        # Set once the scene is read; lib3mf scenes use their lighter face weight.
        self.lib3mf_scene = False
        self.scene_cleanup_pending = False
        self.fingerprint_result: FingerprintResult | None = None
        self.sample_buffers_pending = False
        self.geometry_emitted = False
        self.thumbnail_emitted = False
        self.reload_mesh_for_fingerprint = False
        self.stl_measurements: STLMeasurements | None = None
        self.embedded = None
        self.visual_mesh = None

    def coverage(self) -> MeshCoverage:
        return MeshCoverage(
            self.source_scan, self.geometry_representation, self.preview_coverage
        )

    def deliver(self, output: BasicOutput) -> None:
        if self.on_output is not None:
            try:
                self.on_output(output)
            except Exception as exc:
                raise _OutputDeliveryError(exc) from exc

    def emit_geometry(self) -> None:
        if not self.request.include_geometry or self.geometry_emitted:
            return
        if self.geometry["triangle_count"] is not None:
            self.geometry_outcome = GeometryReady()
        if isinstance(self.geometry_outcome, GeometryNotRequested):
            raise ValueError("requested geometry has no outcome")
        self.geometry_emitted = True
        if self.on_output is not None:
            self.deliver(
                GeometryOutput(
                    dict(self.geometry),
                    self.geometry_outcome,
                    self.volume,
                    self.coverage(),
                    max(round((time.monotonic() - self.started) * 1000), 0),
                    _peak_rss_bytes(),
                )
            )

    def emit_thumbnail(self) -> None:
        if not self.request.include_thumbnail or self.thumbnail_emitted:
            return
        if self.strategy is not ThumbnailStrategy.EMBEDDED:
            # A rendered picture follows the geometry it was rendered from.
            # One the document supplied owes the mesh nothing.
            self.emit_geometry()
        if self.image is not None:
            self.failure = None
        self.thumbnail_emitted = True
        if self.packet is not None:
            self.deliver(
                PreparedThumbnailOutput(
                    self.packet,
                    self.coverage(),
                    max(round((time.monotonic() - self.started) * 1000), 0),
                    _peak_rss_bytes(),
                )
            )
            return
        if self.on_output is not None:
            self.deliver(
                ThumbnailOutput(
                    self.image,
                    self.strategy,
                    self.coverage(),
                    self.failure,
                    max(round((time.monotonic() - self.started) * 1000), 0),
                    _peak_rss_bytes(),
                )
            )

    def report(self, label: str) -> None:
        if self.request.report is not None:
            self.request.report(label)

    def run(self) -> ThumbnailResult | PreparedThumbnailResult:
        """Run every stage the request asks for and return what it came to."""
        self._estimate()
        try:
            with mesh_policy.render_admission():
                self._embedded()
                # A thumbnail-only repair can return a validated embedded image
                # without parsing the mesh archive. Ingestion still loads safe
                # meshes once because it also needs exact geometry metadata.
                if (
                    self.embedded is not None
                    and not self.request.include_geometry
                    and not self.request.include_fingerprint
                ):
                    self.image = self.embedded
                    self.strategy = ThumbnailStrategy.EMBEDDED
                    self.preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
                else:
                    if not self.over_cap:
                        self._parse()
                    self._measure()
                    self._render()
                    self._fingerprint()
        except _OutputDeliveryError as exc:
            raise exc.error from None
        except InvalidMeshMeasurements:
            self.phases.fail_active()
            if not self.geometry_emitted:
                self.geometry = _empty_geometry()
            if self.request.include_geometry and not self.geometry_emitted:
                self.geometry_outcome = GeometryRefused(
                    ThumbnailFailureReason.INVALID_SOURCE
                )
                self.volume = VolumeNotCalculated(
                    VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
                )
            if self.request.include_fingerprint:
                self.fingerprint_result = FingerprintResult(
                    FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode.INVALID_SOURCE,
                )
            if self.embedded is not None:
                self.image = self.embedded
                self.strategy = ThumbnailStrategy.EMBEDDED
                self.preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
            elif self.request.include_thumbnail:
                self.failure = ThumbnailFailureReason.INVALID_SOURCE
        except MemoryError:
            self.phases.fail_active()
            self.prepared = None
            self.detached_prepared = None
            self.mesh = None
            self.source_scene = None
            self.sample_buffers_pending = False
            self.scene_cleanup_pending = False
            mesh_policy.reclaim_memory()
            if self.request.include_geometry and not isinstance(
                self.geometry_outcome, GeometryReady
            ):
                self.geometry_outcome = GeometryRefused(
                    ThumbnailFailureReason.RESOURCE_LIMIT
                )
            if self.request.include_fingerprint:
                self.fingerprint_result = FingerprintResult(
                    FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode.RESOURCE_LIMIT,
                )
            if self.embedded is not None:
                self.image = self.embedded
                self.strategy = ThumbnailStrategy.EMBEDDED
                self.preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
            else:
                self.failure = ThumbnailFailureReason.RESOURCE_LIMIT
        finally:
            self.phases.fail_active()
            self.prepared = None
            self.detached_prepared = None
            self.release_buffers = (
                self.mesh is not None
                or self.source_scene is not None
                or self.sample_buffers_pending
                or self.scene_cleanup_pending
            )
            self.mesh = None
            self.source_scene = None
            if self.release_buffers:
                mesh_policy.reclaim_memory()
        return self._finish()

    def _estimate(self) -> None:
        """Refuse an invalid request and decide whether the source is over its cap."""
        if (
            type(self.request.triangle_cap) is not int
            or not 100 <= self.request.triangle_cap <= MAX_ANALYSIS_FACES
        ):
            raise ValueError("invalid_triangle_cap")
        self.phases.start(MeshPhase.ADMISSION, input_bytes=self.source_bytes)
        self.report("loading_mesh")
        if self.request.file_type is None:
            self.over_cap = mesh_policy.exceeds_cap(self.request.path)
        else:
            self.over_cap = mesh_policy.exceeds_cap(
                self.request.path, file_type=self.suffix
            )
        self.phases.finish()

        if self.over_cap and self.request.include_geometry:
            self.geometry_outcome = GeometryRefused(
                ThumbnailFailureReason.RESOURCE_LIMIT
            )

    def _embedded(self) -> None:
        """Take a 3MF's own picture, and publish it before the mesh is read."""
        if (
            self.request.include_thumbnail
            and self.suffix == ".3mf"
            and (not self.over_cap or settings.use_embedded_3mf_preview_for_large_files)
        ):
            self.phases.start(MeshPhase.EMBEDDED, input_bytes=self.source_bytes)
            self.embedded = mesh_previews.extract_embedded_3mf_thumbnail(
                self.request.path,
                validate_image=True,
                file_type=self.suffix if self.request.file_type is not None else None,
            )

            self.phases.finish(
                output_bytes=len(self.embedded) if self.embedded is not None else None
            )
            if self.embedded is not None:
                # The slicer's own picture is ready now. Publish it
                # before the mesh is read, so the Model has a thumbnail
                # while its geometry is still being measured.
                self.image = self.embedded
                self.strategy = ThumbnailStrategy.EMBEDDED
                self.preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
                self.emit_thumbnail()

    def _parse(self) -> None:
        """Read the source into the representation its format calls for."""
        self.phases.start(MeshPhase.LOAD, input_bytes=self.source_bytes)
        if self.suffix == ".3mf":
            # Retained scenes admit unique source buffers and
            # placed counts without allocating a whole mesh.
            # Render, topology and FP apply their own budgets
            # before a consumer materializes placed geometry.
            try:
                scene, reader = read_scene_with_reader(
                    self.request.path, max_faces=MAX_ANALYSIS_FACES
                )
                self.source_scene = PreparedScene(scene)
                self.lib3mf_scene = reader is ThreeMFReader.LIB3MF
            except GeometryError as exc:
                if exc.code in {
                    "archive_resource_limit",
                    "resource_limit",
                    "scene_resource_limit",
                }:
                    self.over_cap = True
                refusal = (
                    ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
                    if isinstance(exc, Unsupported3MFCapability)
                    else ThumbnailFailureReason.RESOURCE_LIMIT
                    if self.over_cap
                    else ThumbnailFailureReason.INVALID_SOURCE
                )
                self.failure = refusal
                if self.request.include_geometry:
                    self.geometry_outcome = GeometryRefused(refusal)
                if self.request.include_fingerprint:
                    self.fingerprint_result = FingerprintResult(
                        FingerprintResultState.UNSUPPORTED
                        if isinstance(exc, Unsupported3MFCapability)
                        else FingerprintResultState.FAILED,
                        failure_code=FingerprintFailureCode(exc.code),
                    )
        elif self.request.include_fingerprint and self.suffix in (
            ".step",
            ".stp",
        ):
            try:
                self.mesh = mesh_loading.load_step_mesh(
                    self.request.path, include_brep=True
                )
            except GeometryError as exc:
                if self.request.include_geometry:
                    self.geometry_outcome = GeometryRefused(
                        ThumbnailFailureReason.UNSUPPORTED_FORMAT
                        if exc.code == "step_unavailable"
                        else ThumbnailFailureReason.TIMEOUT
                        if exc.code == "tessellation_timeout"
                        else ThumbnailFailureReason.RESOURCE_LIMIT
                        if exc.code in {"worker_oom", "geometry_work_limit"}
                        else ThumbnailFailureReason.INVALID_SOURCE
                    )
                self.fingerprint_result = FingerprintResult(
                    FingerprintResultState.UNSUPPORTED
                    if exc.code == "step_unavailable"
                    else FingerprintResultState.FAILED,
                    failure_code=FingerprintFailureCode(exc.code),
                )
        else:
            try:
                array_source = not self.request.include_fingerprint and (
                    self.suffix == ".stl"
                    or (self.suffix == ".obj" and not self.request.include_geometry)
                )
                if array_source:
                    from .visual_loading import load_visual_mesh

                    self.visual_mesh = load_visual_mesh(self.request.path, self.suffix)
                else:
                    if (
                        self.suffix == ".obj"
                        and not self.request.include_fingerprint
                        and self.request.include_geometry
                    ):
                        from .obj_source import load_obj_source

                        self.visual_mesh = load_obj_source(self.request.path)
                    self.mesh = (
                        None
                        if self.visual_mesh is not None
                        else (
                            mesh_loading.load_mesh(self.request.path)
                            if self.request.file_type is None
                            else mesh_loading.load_mesh(
                                self.request.path, file_type=self.suffix
                            )
                        )
                    )
            except GeometryError as exc:
                self.over_cap = exc.code == "resource_limit"
                refusal = (
                    ThumbnailFailureReason.RESOURCE_LIMIT
                    if self.over_cap
                    else ThumbnailFailureReason.INVALID_SOURCE
                )
                self.failure = refusal
                if self.request.include_geometry:
                    self.geometry_outcome = GeometryRefused(refusal)
                if self.request.include_fingerprint:
                    self.fingerprint_result = FingerprintResult(
                        FingerprintResultState.FAILED,
                        failure_code=FingerprintFailureCode(exc.code),
                    )

        self.phases.finish(
            outcome=PhaseOutcome.COMPLETED
            if self.mesh is not None
            or self.source_scene is not None
            or self.visual_mesh is not None
            else PhaseOutcome.FAILED,
            triangle_count=self.source_scene.triangle_count
            if self.source_scene is not None
            else len(self.mesh.faces)
            if self.mesh is not None
            else len(self.visual_mesh.faces)
            if self.visual_mesh is not None
            else None,
        )

    def _measure(self) -> None:
        """Measure geometry and volume from what was read, and publish them."""
        if self.source_scene is not None:
            self.source_scan = SourceScanState.COMPLETE
        elif self.mesh is not None or self.visual_mesh is not None:
            self.source_scan = SourceScanState.COMPLETE
            self.geometry_representation = CompleteGeometry()

        self.report("extracting_geometry")
        if self.request.include_geometry:
            self.phases.start(MeshPhase.MEASUREMENTS)
            if self.over_cap and self.suffix == ".stl":
                try:
                    measured = scan_stl(self.request.path)
                except (InvalidSTL, OSError) as exc:
                    self.geometry_outcome = GeometryRefused(
                        ThumbnailFailureReason.RESOURCE_LIMIT
                        if isinstance(exc, InvalidSTL)
                        and exc.reason is STLReadFailure.RESOURCE_LIMIT
                        else ThumbnailFailureReason.INVALID_SOURCE
                    )
                else:
                    self.stl_measurements = measured
                    self.source_scan = SourceScanState.COMPLETE
                    self.volume = VolumeNotCalculated(
                        VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                    )
                    self.geometry.update(
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
            elif self.source_scene is not None:
                from app.modules.media.scene_measurements import (
                    VolumeTopologyRequired,
                    measure_scene,
                )

                # Unique-resource topology caches also own native cycles.
                # Release them before rendering, even without fingerprints.
                self.scene_cleanup_pending = True
                try:
                    scene_measurements = measure_scene(self.source_scene.scene)
                except GeometryError as exc:
                    # Source syntax may be valid while placed coordinates
                    # exceed the numeric range. Embedded output is independent.
                    self.scene_cleanup_pending = True
                    self.source_scene = None
                    self.geometry = _empty_geometry()
                    self.volume = VolumeNotCalculated(
                        VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
                    )
                    self.geometry_outcome = GeometryRefused(
                        ThumbnailFailureReason.INVALID_SOURCE
                    )
                    if self.request.include_fingerprint:
                        self.fingerprint_result = FingerprintResult(
                            FingerprintResultState.FAILED,
                            failure_code=FingerprintFailureCode(exc.code),
                        )
                else:
                    self.geometry = scene_measurements.geometry
                    scene_volume = scene_measurements.volume
                    if isinstance(scene_volume, VolumeTopologyRequired):
                        self.volume = VolumeNotCalculated(
                            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                        )
                        if (
                            self.source_scene.triangle_count
                            <= mesh_policy.load_face_budget(
                                self.suffix, lib3mf=self.lib3mf_scene
                            )
                        ):
                            self.scene_cleanup_pending = True
                            try:
                                self.prepared = mesh_resources.materialize_scene(
                                    self.source_scene.scene
                                )
                            except (GeometryError, MemoryError) as exc:
                                # A failed topology attempt cannot withdraw
                                # independent bounds/count or force an FP retry.
                                if self.request.include_fingerprint:
                                    self.fingerprint_result = FingerprintResult(
                                        FingerprintResultState.FAILED,
                                        failure_code=FingerprintFailureCode(exc.code)
                                        if isinstance(exc, GeometryError)
                                        else FingerprintFailureCode.RESOURCE_LIMIT,
                                    )
                            else:
                                self.geometry_representation = CompleteGeometry()
                                self.volume = mesh_measurements.geometry_from_mesh(
                                    self.prepared.whole_mesh
                                ).volume
                                self.geometry["volume_mm3"] = volume_value(self.volume)
                    else:
                        self.volume = scene_volume
            elif self.visual_mesh is not None:
                from .array_measurements import geometry_from_arrays

                measured = geometry_from_arrays(self.visual_mesh)
                self.geometry, self.volume = measured.geometry, measured.volume
            else:
                measured = mesh_measurements.geometry_from_mesh(self.mesh)
                self.geometry, self.volume = measured.geometry, measured.volume
            validate_geometry_extents(self.geometry)
            self.phases.finish(
                triangle_count=int(self.geometry["triangle_count"])
                if self.geometry["triangle_count"] is not None
                else None,
                outcome=PhaseOutcome.COMPLETED
                if self.geometry["triangle_count"] is not None
                else PhaseOutcome.FAILED,
            )
            if self.geometry["triangle_count"] is not None:
                self.geometry_outcome = GeometryReady()
            elif self.over_cap and (
                self.suffix != ".stl" or self.request.include_thumbnail
            ):
                self.geometry_outcome = GeometryRefused(
                    ThumbnailFailureReason.RESOURCE_LIMIT
                )

        self.emit_geometry()

    def _render(self) -> None:
        """Produce the thumbnail, or the packet a renderer will finish, and publish it."""
        if self.request.include_thumbnail:
            self.phases.start(MeshPhase.RENDER)
            self.report("rendering_thumbnail")
            if self.embedded is not None:
                self.image = self.embedded
                self.strategy = ThumbnailStrategy.EMBEDDED
                self.preview_coverage = PreviewCoverage.DOCUMENT_SUPPLIED
            elif (
                self.mesh is not None
                or self.visual_mesh is not None
                or self.source_scene is not None
            ):
                if self.source_scene is not None:
                    # Keep only already-admitted placed arrays for later
                    # analysis; native mesh objects and topology caches
                    # are released before retained-scene rendering.
                    if (
                        self.prepared is not None
                        and self.request.include_fingerprint
                        and self.fingerprint_result is None
                        and len(self.prepared.whole_mesh.faces)
                        <= self.request.triangle_cap
                    ):
                        self.detached_prepared = mesh_resources.detach_scene_mesh(
                            self.prepared, triangle_cap=self.request.triangle_cap
                        )
                    self.prepared = None
                    self.mesh = None
                    if self.scene_cleanup_pending:
                        self.scene_cleanup_pending = False
                        mesh_policy.reclaim_memory()
                if self.source_scene is not None:
                    render_triangles = self.source_scene.triangle_count
                else:
                    render_mesh = (
                        self.visual_mesh if self.visual_mesh is not None else self.mesh
                    )
                    assert render_mesh is not None
                    render_triangles = len(render_mesh.faces)
                cap = int(settings.mesh_max_render_triangles)
                ram_cap = mesh_policy.ram_triangle_cap(
                    self.suffix, lib3mf=self.lib3mf_scene
                )
                if ram_cap is not None:
                    cap = min(cap, ram_cap)
                if render_triangles > cap:
                    self.failure = ThumbnailFailureReason.RESOURCE_LIMIT
                    logger.warning(
                        "thumbnail_engine: post-load triangle budget exceeded",
                        extra={
                            "strategy": "full",
                            "triangles": render_triangles,
                        },
                    )
                else:
                    try:
                        if self.output_directory is not None:
                            render_input = (
                                mesh_render.prepare_scene_packet(
                                    self.source_scene.scene
                                )
                                if self.source_scene is not None
                                else mesh_render.prepare_preview_packet(
                                    self.visual_mesh
                                    if self.visual_mesh is not None
                                    else self.mesh
                                )
                            )
                            from .thumbnail_pixels import check_deadline

                            assert self.preparation_deadline is not None
                            check_deadline(self.preparation_deadline, force=True)
                            # The supervisor selected this handoff. Release
                            # parser credit before the broker checks actual
                            # units and current GPU eligibility at dispatch.
                            # Refusal renders the same packet under guarded
                            # CPU admission, without reopening the source.
                            if render_input is not None:
                                try:
                                    self.packet = write_packet(
                                        render_input,
                                        self.output_directory,
                                        self.width,
                                        self.height,
                                    )
                                except ValueError:
                                    # A source may fit native CPU rendering while its
                                    # immutable packet exceeds the shared wire budget.
                                    self.packet = None
                        self.image = (
                            None
                            if self.packet is not None
                            else (
                                mesh_render.render_scene_thumbnail(
                                    self.source_scene.scene,
                                    self.request.path.name,
                                    width=self.width,
                                    height=self.height,
                                    output_format=self.request.output_format,
                                )
                                if self.source_scene is not None
                                else mesh_render.render_mesh_thumbnail(
                                    self.visual_mesh
                                    if self.visual_mesh is not None
                                    else self.mesh,
                                    self.request.path.name,
                                    width=self.width,
                                    height=self.height,
                                    output_format=self.request.output_format,
                                )
                            )
                        )
                    except OperationCancelled, TimeoutError:
                        raise
                    except Exception:  # noqa: BLE001 - bounded fallbacks remain
                        logger.exception(
                            "thumbnail_engine: full renderer failed",
                            extra={"format": self.suffix},
                        )
                    if self.image is not None:
                        self.strategy = ThumbnailStrategy.FULL
                        self.preview_coverage = PreviewCoverage.COMPLETE

            self.phases.finish(
                output_bytes=len(self.image)
                if self.image is not None
                else self.packet.size_bytes
                if self.packet is not None
                else None,
                outcome=PhaseOutcome.COMPLETED
                if self.image is not None or self.packet is not None
                else PhaseOutcome.FAILED,
            )

            if (
                self.image is None
                and self.packet is None
                and self.suffix == ".stl"
                and (
                    self.over_cap
                    or self.mesh is not None
                    or self.visual_mesh is not None
                )
            ):
                self.release_buffers = (
                    self.mesh is not None
                    or self.visual_mesh is not None
                    or self.sample_buffers_pending
                )
                self.visual_mesh = None
                self.reload_mesh_for_fingerprint = self.mesh is not None
                self.prepared = None
                self.mesh = None
                self.sample_buffers_pending = False
                if self.release_buffers:
                    mesh_policy.reclaim_memory()
                self.phases.start(MeshPhase.STREAMING, input_bytes=self.source_bytes)
                from app.modules.media import stl_streaming

                streamed = (
                    stl_streaming.render_stl_preview_isolated(
                        self.request.path,
                        width=self.width,
                        height=self.height,
                        measurements=self.stl_measurements,
                    )
                    if self.stl_measurements is not None
                    and os.environ.get(WORKER_MARKER) == str(os.getpid())
                    else stl_streaming.render_stl_preview_isolated(
                        self.request.path, width=self.width, height=self.height
                    )
                )
                self.phases.finish(
                    output_bytes=len(streamed.png) if streamed is not None else None,
                    triangle_count=streamed.triangle_count
                    if streamed is not None
                    else None,
                    outcome=PhaseOutcome.COMPLETED
                    if streamed is not None
                    else PhaseOutcome.FAILED,
                )
                if streamed is not None:
                    self.image = streamed.png
                    self.strategy = ThumbnailStrategy.STREAMING
                    self.source_scan = SourceScanState.COMPLETE
                    self.preview_coverage = PreviewCoverage.COMPLETE
                    if (
                        self.request.include_geometry
                        and not self.geometry_emitted
                        and self.geometry["triangle_count"] is None
                    ):
                        self.volume = VolumeNotCalculated(
                            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                        )
                        self.geometry.update(
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

            if self.image is None and self.packet is None and self.suffix == ".stl":
                self.phases.start(MeshPhase.FALLBACK, input_bytes=self.source_bytes)
                fallback = stl_fallback.render_stl_thumbnail(
                    self.request.path, width=self.width, height=self.height
                )
                self.phases.finish(
                    output_bytes=len(fallback.png) if fallback is not None else None,
                    triangle_count=fallback.triangle_count
                    if fallback is not None
                    else None,
                    outcome=PhaseOutcome.COMPLETED
                    if fallback is not None
                    else PhaseOutcome.FAILED,
                )
                if fallback is not None:
                    self.image = fallback.png
                    self.strategy = ThumbnailStrategy.FALLBACK
                    self.preview_coverage = (
                        PreviewCoverage.COMPLETE
                        if fallback.complete
                        else PreviewCoverage.PARTIAL
                    )
                    if fallback.source_complete:
                        self.source_scan = SourceScanState.COMPLETE
                    elif self.source_scan is SourceScanState.NOT_SCANNED:
                        self.source_scan = SourceScanState.PARTIAL
                    if (
                        self.request.include_geometry
                        and not self.geometry_emitted
                        and fallback.source_complete
                        and self.geometry["triangle_count"] is None
                    ):
                        self.volume = VolumeNotCalculated(
                            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
                        )
                        self.geometry.update(
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

            if self.image is None and self.packet is None and self.failure is None:
                self.failure = (
                    ThumbnailFailureReason.RESOURCE_LIMIT
                    if self.over_cap
                    else ThumbnailFailureReason.NO_GEOMETRY
                    if self.mesh is None and self.source_scene is None
                    else ThumbnailFailureReason.RENDERER_NO_OUTPUT
                )
        self.emit_thumbnail()

    def _fingerprint(self) -> None:
        """Extract the similarity fingerprint from the geometry already in hand."""
        if self.request.include_fingerprint and self.fingerprint_result is None:
            self.phases.start(MeshPhase.FINGERPRINT)
            self.report("extracting_fingerprint")
            try:
                if self.reload_mesh_for_fingerprint and self.mesh is None:
                    self.mesh = mesh_loading.load_mesh(
                        self.request.path, file_type=self.suffix
                    )
                # Analysis admission cannot remove useful measurements
                # or a preview from a mesh admitted by the load budget.
                if (
                    self.source_scene is not None
                    and self.source_scene.triangle_count > self.request.triangle_cap
                    or self.mesh is not None
                    and len(self.mesh.faces) > self.request.triangle_cap
                ):
                    raise GeometryError("geometry_work_limit")
                if self.prepared is None and self.detached_prepared is not None:
                    self.prepared = mesh_resources.restore_scene_mesh(
                        self.detached_prepared
                    )
                if self.prepared is None and self.source_scene is not None:
                    self.scene_cleanup_pending = True
                    self.prepared = mesh_resources.materialize_scene(
                        self.source_scene.scene
                    )
                    self.geometry_representation = CompleteGeometry()
                if self.prepared is None and self.mesh is not None:
                    self.prepared = prepare_loaded_mesh(
                        self.mesh, file_type=self.suffix.lstrip(".")
                    )
                if self.prepared is None and self.suffix == ".stl" and self.over_cap:
                    self.sample_buffers_pending = True
                    sampled_preparation = _prepare_sampled_stl(
                        self.request.path, triangle_cap=self.request.triangle_cap
                    )
                    if sampled_preparation is not None:
                        self.prepared, sampled_scan = sampled_preparation
                        del sampled_preparation
                        if self.source_scan is not SourceScanState.COMPLETE:
                            self.source_scan = sampled_scan
                if (
                    self.prepared is not None
                    and len(self.prepared.whole_mesh.faces) > self.request.triangle_cap
                ):
                    raise GeometryError("geometry_work_limit")
                if self.prepared is not None and isinstance(
                    self.geometry_representation, GeometryNotLoaded
                ):
                    self.geometry_representation = self.prepared.geometry
                    if isinstance(self.prepared.geometry, CompleteGeometry):
                        self.source_scan = SourceScanState.COMPLETE
                self.fingerprint_result = (
                    extract(self.prepared)
                    if self.prepared is not None
                    else FingerprintResult(
                        FingerprintResultState.UNSUPPORTED
                        if self.suffix in (".step", ".stp")
                        else FingerprintResultState.FAILED,
                        failure_code=FingerprintFailureCode.STEP_UNAVAILABLE
                        if self.suffix in (".step", ".stp")
                        else FingerprintFailureCode.RESOURCE_LIMIT
                        if self.over_cap
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
                self.fingerprint_result = FingerprintResult(
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
                self.prepared = None
                self.detached_prepared = None

            self.phases.finish(
                outcome=PhaseOutcome.COMPLETED
                if self.fingerprint_result.state is FingerprintResultState.READY
                else PhaseOutcome.FAILED
            )

    def _finish(self) -> ThumbnailResult | PreparedThumbnailResult:
        """Publish what is still owed, record metrics and build the result."""
        try:
            self.emit_geometry()
            self.emit_thumbnail()
        except _OutputDeliveryError as exc:
            raise exc.error from None
        if (
            self.request.include_geometry
            and self.geometry["triangle_count"] is not None
        ):
            self.geometry_outcome = GeometryReady()
        if self.image is not None or self.packet is not None:
            self.failure = None
        duration_ms = max(round((time.monotonic() - self.started) * 1000), 0)
        labels = {
            "format": self.suffix.removeprefix(".") or "unknown",
            "strategy": self.strategy.value,
            "outcome": "generated"
            if self.image is not None or self.packet is not None
            else "failed",
            "reason": self.failure.value if self.failure is not None else "none",
        }
        self.metrics.increment("thumbnail_generation_total", labels=labels)
        self.metrics.observe(
            "thumbnail_generation_duration_ms", float(duration_ms), labels=labels
        )
        try:
            self.metrics.observe(
                "thumbnail_input_bytes",
                float(self.request.path.stat().st_size),
                labels=labels,
            )
        except OSError:
            pass
        if self.image is not None:
            self.metrics.observe(
                "thumbnail_renderer_output_bytes", float(len(self.image)), labels=labels
            )
        triangles = self.geometry.get("triangle_count")
        if triangles is not None:
            self.metrics.observe("thumbnail_triangles", float(triangles), labels=labels)
        peak_rss = _peak_rss_bytes()
        if peak_rss is not None:
            self.metrics.observe(
                "thumbnail_peak_rss_bytes", float(peak_rss), labels=labels
            )
        result = ThumbnailResult(
            image=self.image,
            geometry=self.geometry,
            geometry_outcome=self.geometry_outcome,
            volume=self.volume,
            strategy=self.strategy,
            coverage=MeshCoverage(
                self.source_scan, self.geometry_representation, self.preview_coverage
            ),
            failure_reason=self.failure,
            duration_ms=duration_ms,
            peak_rss_bytes=peak_rss,
            fingerprint_result=self.fingerprint_result,
            phase_stats=self.phases.snapshot(),
        )
        return (
            PreparedThumbnailResult(result, self.packet)
            if self.packet is not None
            else result
        )


@dataclass
class ThumbnailEngine:
    metrics: ThumbnailMetricsSink = field(default_factory=NoopThumbnailMetrics)

    def generate(
        self, request: ThumbnailRequest, *, on_output: OutputSink | None = None
    ) -> ThumbnailResult:
        result = self._generate(request, on_output=on_output)
        assert isinstance(result, ThumbnailResult)
        return result

    def prepare(
        self,
        request: ThumbnailRequest,
        directory: Path,
        *,
        deadline: float,
        on_output: OutputSink | None = None,
    ) -> ThumbnailResult | PreparedThumbnailResult:
        from .thumbnail_pixels import check_deadline

        check_deadline(deadline, force=True)
        result = self._generate(
            request,
            on_output=on_output,
            output_directory=directory,
            preparation_deadline=deadline,
        )
        check_deadline(deadline, force=True)
        return result

    def _generate(
        self,
        request: ThumbnailRequest,
        *,
        on_output: OutputSink | None = None,
        output_directory: Path | None = None,
        preparation_deadline: float | None = None,
    ) -> ThumbnailResult | PreparedThumbnailResult:
        return _Generation(
            self.metrics,
            request,
            on_output=on_output,
            output_directory=output_directory,
            preparation_deadline=preparation_deadline,
        ).run()


__all__ = [
    "NoopThumbnailMetrics",
    "ThumbnailEngine",
]
