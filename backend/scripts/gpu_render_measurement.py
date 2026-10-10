"""One prepared-source CPU/GPU experiment; no admission or private-vault setup.

Frozen visual tolerances precede candidate images. Requested allocations, RSS
and sampled device telemetry have distinct scopes; none proves context VRAM.
"""

from __future__ import annotations

import hashlib
import resource
import sys
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.render_backend import Candidate, GpuError

if TYPE_CHECKING:
    from printstash_core.mesh.rasterizer import RenderedPixels


POLICY = {
    "foreground_alpha_threshold": 128,
    "foreground_mask_differing_pixels": 0,
    "rgba_max_difference": 8,
    "reason": "half-opacity foreground excludes LANCZOS fringes; RGBA bound is 8/255, fixed before candidate images",
}


class Mode(StrEnum):
    CPU = "cpu"
    COLD = "cold"
    REUSED = "reused"


class Flow(StrEnum):
    PREVIEW = "preview"
    MULTIVIEW = "multiview"
    ANALYTIC = "analytic"


class OutputFormat(StrEnum):
    PNG = "PNG"
    WEBP = "WEBP"


@dataclass(frozen=True)
class PilotSpec:
    source: str
    output: str
    case: str | None
    mode: Mode
    trials: int
    backend: str
    chunk_size: int
    allocation_limit: int
    width: int
    height: int
    views: int
    flow: Flow = Flow.PREVIEW
    embedding_size: int = 224
    output_format: OutputFormat = OutputFormat.WEBP
    candidate: Candidate = Candidate.MODERNGL
    selector: str | None = None
    allow_software: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mode, Mode)
            or not isinstance(self.candidate, Candidate)
            or self.backend != ("auto" if self.candidate is Candidate.WGPU else "egl")
            or type(self.allow_software) is not bool
            or (
                self.selector is not None
                and (type(self.selector) is not str or not self.selector)
            )
            or not isinstance(self.flow, Flow)
            or not isinstance(self.output_format, OutputFormat)
        ):
            raise ValueError("invalid_gpu_pilot_mode")
        integers = (
            self.trials,
            self.chunk_size,
            self.allocation_limit,
            self.width,
            self.height,
            self.views,
            self.embedding_size,
        )
        if any(type(value) is not int or value <= 0 for value in integers):
            raise ValueError("invalid_gpu_pilot_dimensions")
        if not 32 <= self.embedding_size <= 512:
            raise ValueError("invalid_embedding_size")
        if self.flow is not Flow.ANALYTIC and self.views != 1:
            raise ValueError("views_require_analytic_flow")
        if self.flow is Flow.MULTIVIEW and (
            (self.width, self.height) != (640, 480)
            or self.output_format is not OutputFormat.WEBP
        ):
            raise ValueError("multiview_requires_canonical_thumbnail")
        if self.trials > 100 or self.views > 6:
            raise ValueError("invalid_gpu_pilot_trial_count")
        if not Path(self.source).is_absolute() or not Path(self.output).is_absolute():
            raise ValueError("gpu_pilot_requires_absolute_paths")
        if self.case is not None:
            from scripts.viewer_representation_corpus import source_builders

            if self.case not in source_builders():
                raise ValueError("unknown_gpu_pilot_control")


def exception_details(exc: Exception) -> dict[str, object]:
    chain = []
    cause = exc.__cause__
    for _ in range(4):
        if cause is None:
            break
        message = str(cause)
        chain.append(
            {
                "type": type(cause).__name__,
                "message": message[:4096],
                "message_truncated": len(message) > 4096,
            }
        )
        cause = cause.__cause__
    return {
        "diagnostic": f"{type(exc).__name__}: {exc}",
        "native_causes": chain,
        "additional_causes_omitted": cause is not None,
    }


class RenderDiagnostics:
    """Retain a renderer's original caught exception at its logging boundary."""

    def __init__(self) -> None:
        self.failure: Exception | None = None

    def error(self, msg: object, *args: object) -> None:
        current = sys.exception()
        if isinstance(current, Exception):
            self.failure = current

    def warning(self, msg: object, *args: object, exc_info: bool = False) -> None:
        if exc_info:
            self.error(msg, *args)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def library_versions() -> dict[str, str | None]:
    result = {}
    for name in (
        "numpy",
        "trimesh",
        "scipy",
        "Pillow",
        "moderngl",
        "glcontext",
        "wgpu",
        "rendercanvas",
    ):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def compare_pixels(
    reference: RenderedPixels, candidate: RenderedPixels
) -> dict[str, object]:
    """Compare complete output planes; equal blank images cannot pass."""
    import numpy as np

    if (reference.width, reference.height) != (candidate.width, candidate.height):
        raise ValueError("pixel_dimensions_mismatch")
    a = np.frombuffer(reference.rgba, dtype=np.uint8).reshape(-1, 4)
    b = np.frombuffer(candidate.rgba, dtype=np.uint8).reshape(-1, 4)
    difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
    foreground_a, foreground_b = a[:, 3] >= 128, b[:, 3] >= 128
    mask = int(np.count_nonzero(foreground_a != foreground_b))
    maximum = int(difference.max())
    visible = bool(foreground_a.any() and foreground_b.any())
    return {
        "accepted": visible and mask == 0 and maximum <= 8,
        "both_have_foreground": visible,
        "foreground_mask_differing_pixels": mask,
        "rgba_max_difference": maximum,
        "rgba_mean_difference": float(difference.mean()),
        "reference_rgba_sha256": hashlib.sha256(reference.rgba).hexdigest(),
        "candidate_rgba_sha256": hashlib.sha256(candidate.rgba).hexdigest(),
    }


def measure(spec: PilotSpec) -> dict[str, object]:
    started = time.perf_counter()
    import numpy as np
    from numpy.typing import NDArray
    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
    from printstash_core.mesh.rasterizer import (
        RGBBackground,
        encode_rendered_pixels,
        render_prepared_pixels,
    )
    from printstash_core.mesh.render_geometry import (
        prepare_mesh_render,
        prepare_scene_render,
    )

    from app.core.cancellation import checkpoint
    from app.modules.media import thumbnail
    from app.modules.media.geometry_analysis import canonical_frames, thumbnail_input
    from app.modules.media.mesh_loading import load_mesh
    from app.modules.media.three_mf_scene import read_scene

    imported = time.perf_counter()
    source, output = Path(spec.source), Path(spec.output)
    output.mkdir(parents=True, exist_ok=True)
    if spec.case is not None and not source.exists():
        from scripts.viewer_representation_corpus import write_sources

        source = write_sources(source.parent, (spec.case,))[spec.case]
    digest = _digest(source)
    loaded_start = time.perf_counter()
    if source.suffix.lower() == ".3mf" and spec.flow is not Flow.MULTIVIEW:
        loaded = read_scene(source)
        loaded_end = time.perf_counter()
        prepared = prepare_scene_render(loaded, face_chunk_size=spec.chunk_size)
    else:
        loaded = load_mesh(source, file_type=source.suffix[1:])
        loaded_end = time.perf_counter()
        prepared = prepare_mesh_render(loaded, face_chunk_size=spec.chunk_size)
    if prepared is None:
        raise ValueError("source_has_no_render_geometry")
    prepared_end = time.perf_counter()
    del loaded
    radius = float(np.linalg.norm(np.ptp(prepared.vertices, axis=0))) + 1.0
    # The production flow has one preview and six fixed matte encoder cameras.
    cameras: list[NDArray[np.float64] | None] = [None]
    if spec.flow is Flow.MULTIVIEW:
        cameras.extend(
            np.asarray(item, dtype=np.float64) for item in canonical_frames()
        )
    elif spec.flow is Flow.ANALYTIC and spec.views > 1:
        cameras = []
        for view in range(spec.views):
            angle = view * 2 * np.pi / spec.views
            cameras.append(
                np.array(
                    [
                        [np.cos(angle), 0, np.sin(angle)],
                        [0, 1, 0],
                        [-np.sin(angle), 0, np.cos(angle)],
                    ]
                )
            )
    observations: list[dict[str, object]] = []
    qualities: list[dict[str, object]] = []
    gpu_hashes: dict[int, str] = {}
    determinism_failures = []
    context = None
    gpu_api = None
    gpu_import_ms = 0.0
    gpu_import_error = None
    if spec.mode != "cpu":
        before = time.perf_counter()
        try:
            if spec.candidate is Candidate.WGPU:
                from scripts import wgpu_render_backend as gpu_api
            else:
                from scripts import gpu_render_backend as gpu_api
        except ImportError as exc:
            gpu_import_error = {
                "reason": "optional_library_unavailable",
                "diagnostic": str(exc),
            }
        gpu_import_ms = (time.perf_counter() - before) * 1000
    try:
        for trial in range(spec.trials):
            checkpoint(force=True)
            samples = {}
            order = (
                ("cpu",)
                if spec.mode == "cpu"
                else (("cpu", "gpu") if trial % 2 == 0 else ("gpu", "cpu"))
            )
            for method in order:
                row: dict[str, object] = {
                    "trial": trial,
                    "method": method,
                    "status": "failed",
                    "frames": [],
                }
                begin = time.perf_counter()
                frame = None
                owned_context = None
                pixels = []
                rgb_planes = []
                frames = []
                images = []
                try:
                    context_ms = 0.0
                    if method == "gpu":
                        if gpu_api is None:
                            raise ImportError(str(gpu_import_error))
                        if context is None:
                            before = time.perf_counter()
                            context = (
                                gpu_api.GpuContext.create(
                                    backend=spec.backend,
                                    selector=spec.selector,
                                    allow_software=spec.allow_software,
                                )
                                if spec.candidate is Candidate.WGPU
                                else gpu_api.GpuContext.create(backend=spec.backend)
                            )
                            context_ms = (time.perf_counter() - before) * 1000
                        row["device"] = context.info
                        if spec.mode == "cold":
                            owned_context, context = context, None
                    active = owned_context or context
                    for view, rotation in enumerate(cameras):
                        checkpoint(force=True)
                        matte = spec.flow is Flow.MULTIVIEW and view > 0
                        width, height = (
                            (spec.embedding_size, spec.embedding_size)
                            if matte
                            else (spec.width, spec.height)
                        )
                        supersample = PREVIEW_PROFILE.supersample_for(width)
                        before = time.perf_counter()
                        if method == "gpu":
                            if gpu_api is None or active is None:
                                raise ValueError("gpu_context_unavailable")
                            frame = gpu_api.GpuFrame(
                                active,
                                width * supersample,
                                height * supersample,
                                spec.chunk_size,
                                radius,
                                matte=matte,
                                allocation_limit_bytes=spec.allocation_limit,
                            )
                        frame_setup_ms = (time.perf_counter() - before) * 1000
                        before = time.perf_counter()
                        diagnostics = RenderDiagnostics()
                        result = render_prepared_pixels(
                            prepared,
                            source.name,
                            width,
                            height,
                            face_chunk_size=spec.chunk_size,
                            rasterise_triangles=frame,
                            logger=diagnostics,
                            view_rotation=rotation,
                            matte=matte,
                        )
                        rendering_ms = (time.perf_counter() - before) * 1000
                        if result is None:
                            if frame is not None and frame.failure is not None:
                                raise frame.failure
                            if diagnostics.failure is not None:
                                raise diagnostics.failure
                            raise ValueError("render_unavailable")
                        before = time.perf_counter()
                        payload = (
                            None
                            if matte
                            else encode_rendered_pixels(
                                result,
                                output_format="WEBP"
                                if spec.output_format is OutputFormat.WEBP
                                else "PNG",
                            )
                        )
                        encoding_ms = (time.perf_counter() - before) * 1000
                        before = time.perf_counter()
                        if spec.flow is Flow.MULTIVIEW and view == 0:
                            if payload is None:
                                raise ValueError("thumbnail_encoding_unavailable")
                            normalized = thumbnail.to_webp(
                                payload, width=640, renderer_encoded=True
                            )
                            rgb = thumbnail_input(normalized, spec.embedding_size).rgb
                            if rgb is None:
                                raise ValueError("thumbnail_rgb_unavailable")
                            rgb_width = rgb_height = spec.embedding_size
                        else:
                            rgb = result.rgb(RGBBackground.WHITE)
                            rgb_width, rgb_height = result.width, result.height
                        conversion_ms = (time.perf_counter() - before) * 1000
                        item: dict[str, object] = {
                            "view": view,
                            "render_and_postprocess_ms": rendering_ms,
                            "frame_allocation_shader_setup_ms": frame_setup_ms,
                            "encoding_ms": encoding_ms,
                            "encoded_bytes": len(payload)
                            if payload is not None
                            else None,
                            "encoded_sha256": hashlib.sha256(payload).hexdigest()
                            if payload is not None
                            else None,
                            "rgb_conversion_ms": conversion_ms,
                            "rgb_sha256": hashlib.sha256(rgb).hexdigest(),
                            "rgb_width": rgb_width,
                            "rgb_height": rgb_height,
                            "width": width,
                            "height": height,
                            "matte": matte,
                        }
                        item["rgba_sha256"] = hashlib.sha256(result.rgba).hexdigest()
                        if method == "gpu":
                            previous = gpu_hashes.setdefault(
                                view, hashlib.sha256(result.rgba).hexdigest()
                            )
                            if previous != item["rgba_sha256"]:
                                determinism_failures.append(
                                    {"trial": trial, "view": view}
                                )
                        if frame is not None:
                            item["gpu"] = asdict(frame.stats)
                            frame.close()
                            frame = None
                        if trial == 0 and payload is not None:
                            images.append(
                                (
                                    output
                                    / f"{method}-view{view}.{spec.output_format.value.lower()}",
                                    payload,
                                )
                            )
                        frames.append(item)
                        pixels.append(result)
                        rgb_planes.append(rgb)
                    row.update(status="completed", frames=frames, context_ms=context_ms)
                    samples[method] = (pixels, rgb_planes)
                except Exception as exc:
                    reason = (
                        exc.reason.value
                        if isinstance(exc, GpuError)
                        else "optional_library_unavailable"
                        if isinstance(exc, ImportError)
                        else "render_failed"
                    )
                    row.update(
                        reason=reason,
                        frames=frames,
                        **exception_details(exc),
                    )
                    if method == "gpu":
                        if frame is not None:
                            row["failed_frame_stats"] = asdict(frame.stats)
                            frame.close()
                            frame = None
                        if context is not None:
                            context.close()
                            context = None
                finally:
                    if frame is not None:
                        frame.close()
                    if owned_context is not None:
                        owned_context.close()
                    row["retained_geometry_visual_ms"] = (
                        time.perf_counter() - begin
                    ) * 1000
                    shared_ms = (
                        (imported - started) + (prepared_end - loaded_start)
                    ) * 1000
                    row["full_cold_source_visual_ms"] = (
                        shared_ms
                        + float(row["retained_geometry_visual_ms"])
                        + (gpu_import_ms if method == "gpu" else 0)
                    )
                    row["rss_high_water_bytes"] = (
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                    )
                    observations.append(row)
                for destination, payload in images:
                    destination.write_bytes(payload)
                if _digest(source) != digest:
                    raise ValueError("source_changed_by_pilot")
            if "cpu" in samples and "gpu" in samples:
                cpu_pixels, cpu_rgb = samples["cpu"]
                gpu_pixels, gpu_rgb = samples["gpu"]
                for view, (cpu, gpu, a, b) in enumerate(
                    zip(cpu_pixels, gpu_pixels, cpu_rgb, gpu_rgb, strict=True)
                ):
                    quality = compare_pixels(cpu, gpu)
                    rgb_difference = np.abs(
                        np.frombuffer(a, dtype=np.uint8).astype(np.int16)
                        - np.frombuffer(b, dtype=np.uint8).astype(np.int16)
                    )
                    rgb_maximum = int(rgb_difference.max())
                    qualities.append(
                        {
                            "trial": trial,
                            "view": view,
                            **quality,
                            "rgb_max_difference": rgb_maximum,
                            "rgb_within_frozen_channel_bound": rgb_maximum <= 8,
                            "accepted": quality["accepted"] and rgb_maximum <= 8,
                        }
                    )
    finally:
        if context is not None:
            context.close()
    return {
        "source": str(source),
        "source_sha256": digest,
        "source_unchanged": _digest(source) == digest,
        "mode": spec.mode,
        "flow": spec.flow,
        "embedding_size": spec.embedding_size,
        "output_format": spec.output_format,
        "triangle_count": prepared.face_count,
        "versions": library_versions(),
        "shared_stages_ms": {
            "imports": (imported - started) * 1000,
            "load": (loaded_end - loaded_start) * 1000,
            "prepare": (prepared_end - loaded_end) * 1000,
            "optional_gpu_import": gpu_import_ms,
        },
        "observations": observations,
        "quality": qualities,
        "gpu_fixed_backend_chunk_determinism_failures": determinism_failures,
        "protected_component_oracle_scope": "separate analytic corpus qualification; not inferred from equal masks",
        "quality_policy": POLICY,
        "gpu_import_error": gpu_import_error,
        "rss_scope": "whole worker high-water mark, cumulative across trials; not per observation or GPU VRAM",
        "timing_scope": "retained-geometry cost includes context/frame/render/readback/postprocess/shared encoding/RGB conversion/release; full cold-source estimate adds measured shared imports/load/prepare and GPU import; frame hashes included, source hashes and diagnostic writes excluded; render/postprocess remains combined",
        "geometry_scope": "multiview uses production whole-mesh materialization and mesh preparation; preview/analytic retain 3MF instances for scene rendering",
        "camera_scope": "multiview: canonical640x480 WEBP plus bicubic preview RGB and six canonical matte WHITE RGB frames; preview: one canonical camera; analytic: Y-axis controls only",
    }
