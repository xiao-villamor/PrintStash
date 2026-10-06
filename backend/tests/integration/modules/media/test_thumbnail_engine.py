"""Real engine previews and stage costs preserve their geometry contracts."""

from __future__ import annotations

import io
import json
import os
import weakref
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
)
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import mesh_policy, mesh_resources, thumbnail_engine
from app.modules.media.fingerprints import FingerprintResultState
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
)
from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.modules.media.worker_bootstrap import WORKER_MARKER
from tests.factories import content
from tests.factories.geometry import tetrahedron, three_mf


@pytest.fixture
def cube(tmp_path: Path) -> Path:
    source = tmp_path / "cube.stl"
    source.write_bytes(content.binary_stl())
    return source


class TestPhaseStats:
    def test_render_reports_encoded_output_size(self, cube: Path) -> None:
        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, include_geometry=False, output_format="WEBP")
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["render"].output_bytes == len(result.image)
        assert phases["render"].elapsed_ns > 0
        assert phases["render"].outcome.value == "completed"

    def test_fingerprint_only_retains_analysis_cost(self, cube: Path) -> None:
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                cube,
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["fingerprint"].elapsed_ns > 0
        assert phases["fingerprint"].outcome.value == "completed"
        assert "render" not in phases

    def test_embedded_preview_retains_extraction_cost(self, tmp_path: Path) -> None:
        source = tmp_path / "embedded.3mf"
        source.write_bytes(three_mf(extras={"Metadata/thumbnail.png": content.png()}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_geometry=False)
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["embedded"].output_bytes == len(result.image)
        assert phases["embedded"].input_bytes == source.stat().st_size
        assert "load" not in phases

    def test_refused_load_retains_failed_stage(self, tmp_path: Path) -> None:
        source = tmp_path / "broken.obj"
        source.write_bytes(b"not geometry")

        result = ThumbnailEngine().generate(ThumbnailRequest(source))

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["load"].outcome.value == "failed"
        assert phases["load"].elapsed_ns > 0
        assert phases["measurements"].triangle_count is None

    def test_streamed_preview_retains_separate_stage(
        self, cube: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, include_geometry=False)
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["streaming"].elapsed_ns > 0
        assert phases["streaming"].output_bytes == len(result.image)
        assert phases["streaming"].triangle_count == 12

    def test_fallback_refusal_retains_attempt_cost(self, tmp_path: Path) -> None:
        source = tmp_path / "broken.stl"
        source.write_bytes(b"not geometry")

        result = ThumbnailEngine().generate(ThumbnailRequest(source))

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["fallback"].outcome.value == "failed"
        assert phases["fallback"].elapsed_ns > 0
        assert phases["fallback"].input_bytes == source.stat().st_size
        assert phases["fallback"].output_bytes is None


class TestMetadataOnly:
    @pytest.mark.parametrize("encoding", ["binary", "ascii"], ids=str)
    def test_measures_stl_without_thumbnail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoding: str
    ) -> None:
        source = tmp_path / "large.stl"
        body = (
            content.binary_stl(triangles=120)
            if encoding == "binary"
            else content.ascii_stl(triangles=120)
        )
        source.write_bytes(body)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False)
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 120
        assert result.geometry["bbox_x_mm"] == pytest.approx(
            3.8 if encoding == "binary" else 1, abs=1e-6
        )
        assert result.geometry["bbox_y_mm"] == pytest.approx(
            29.8 if encoding == "binary" else 1, abs=1e-6
        )
        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.coverage == MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
        )
        assert all(
            stat.phase.value not in {"render", "streaming", "fallback"}
            for stat in result.phase_stats
        )

    def test_leaves_streamed_volume_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "large.stl"
        source.write_bytes(content.binary_stl(triangles=120))
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False)
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["volume_mm3"] is None
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )

    @pytest.mark.parametrize(
        "damage", ["truncated", "trailing", "nonfinite", "incomplete-ascii"], ids=str
    )
    def test_refuses_invalid_metadata_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
    ) -> None:
        source = tmp_path / "invalid.stl"
        body = content.binary_stl(triangles=120)
        if damage == "truncated":
            body = body[:-1]
        elif damage == "trailing":
            body += b"extra"
        elif damage == "nonfinite":
            body = body[:96] + b"\x00\x00\xc0\x7f" + body[100:]
        else:
            body = content.ascii_stl(triangles=120).replace(
                b"endfacet\nendsolid", b"endsolid"
            )
        source.write_bytes(body)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0.000001)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False)
        )

        assert result.geometry_outcome == GeometryRefused(
            ThumbnailFailureReason.INVALID_SOURCE
        )
        assert all(value is None for value in result.geometry.values())


class TestSTLReadCost:
    def test_preview_with_geometry_reads_two_source_passes(
        self,
        cube: Path,
        monkeypatch: pytest.MonkeyPatch,
        count_source_reads: Callable[[Path], list[int]],
    ) -> None:
        """Exercise the same in-process streaming path used inside a native worker."""
        # Exceed the bounded 1 KiB admission probe so full reads are distinct.
        cube.write_bytes(content.binary_stl(triangles=120))
        source_bytes = cube.stat().st_size
        reads = count_source_reads(cube)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        monkeypatch.setenv(WORKER_MARKER, str(os.getpid()))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, width=128, height=128)
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 120
        assert result.image is not None
        assert [count for count in reads if count >= source_bytes] == [
            source_bytes,
            source_bytes,
        ]


class TestThumbnailEngine:
    def test_preserves_translated_3mf_preview(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        origin = tmp_path / "origin.3mf"
        translated = tmp_path / "translated.3mf"
        origin.write_bytes(three_mf(meshes={1: mesh}))
        translated.write_bytes(
            three_mf(
                meshes={1: mesh},
                build=((1, "1 0 0 0 1 0 0 0 1 1000000000 1000000000 1000000000"),),
            )
        )

        original = ThumbnailEngine().generate(
            ThumbnailRequest(origin, width=128, height=128)
        )
        shifted = ThumbnailEngine().generate(
            ThumbnailRequest(translated, width=128, height=128)
        )

        assert original.image is not None and shifted.image is not None
        original_pixels = np.asarray(Image.open(io.BytesIO(original.image)))
        shifted_pixels = np.asarray(Image.open(io.BytesIO(shifted.image)))
        assert np.count_nonzero(original_pixels[..., 3]) > 0
        np.testing.assert_array_equal(shifted_pixels, original_pixels)

    def test_retains_translated_3mf_metadata(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        path = tmp_path / "translated.3mf"
        path.write_bytes(
            three_mf(
                meshes={1: mesh},
                build=((1, "1 0 0 0 1 0 0 0 1 1000000000 1000000000 1000000000"),),
            )
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path, width=64, height=64))

        assert result.geometry == pytest.approx(
            {
                "bbox_x_mm": 10,
                "bbox_y_mm": 10,
                "bbox_z_mm": 10,
                "volume_mm3": 1000,
                "triangle_count": 12,
            }
        )

    @pytest.mark.parametrize("file_type", ["3mf", "stl", "obj"])
    def test_analysis_budget_preserves_basic_outputs(self, tmp_path, file_type):
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
        path = tmp_path / f"sphere.{file_type}"
        encoded = (
            three_mf(meshes={1: mesh})
            if file_type == "3mf"
            else mesh.export(file_type=file_type)
        )
        path.write_bytes(encoded.encode() if isinstance(encoded, str) else encoded)
        baseline = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, height=64)
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path, width=64, height=64, include_fingerprint=True, triangle_cap=100
            )
        )

        assert result.geometry == baseline.geometry
        assert result.geometry["triangle_count"] == 320
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.image == baseline.image
        assert result.image is not None
        assert (
            np.count_nonzero(np.asarray(Image.open(io.BytesIO(result.image)))[..., 3])
            > 0
        )
        assert result.strategy is ThumbnailStrategy.FULL
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        assert result.failure_reason is None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert result.fingerprint_result.failure_code == "geometry_work_limit"
        assert result.fingerprint_result.records == ()

    def test_analysis_budget_preserves_expanded_scene_metadata(self, tmp_path):
        mesh = trimesh.creation.icosphere(subdivisions=1, radius=10)
        path = tmp_path / "assembly.3mf"
        path.write_bytes(
            three_mf(
                meshes={1: mesh}, build=((1, None), (1, "1 0 0 0 1 0 0 0 1 30 0 0"))
            )
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True, triangle_cap=100)
        )

        assert result.geometry["triangle_count"] == 160
        assert result.geometry["bbox_x_mm"] == 50
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.image is not None
        assert result.fingerprint_result.failure_code == "geometry_work_limit"

    def test_analysis_budget_preserves_measurements_without_render(self, tmp_path):
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
        path = tmp_path / "sphere.3mf"
        path.write_bytes(three_mf(meshes={1: mesh}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_thumbnail=False,
                include_fingerprint=True,
                triangle_cap=100,
            )
        )

        assert result.geometry["triangle_count"] == 320
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.image is None
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert isinstance(result.volume, VolumeMeasured)
        assert result.volume.value_mm3 == pytest.approx(mesh.volume)
        assert result.geometry == pytest.approx(
            {
                "triangle_count": 320,
                "bbox_x_mm": 20.0,
                "bbox_y_mm": 20.0,
                "bbox_z_mm": 20.0,
                "volume_mm3": mesh.volume,
            }
        )
        assert result.failure_reason is None
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.GEOMETRY_WORK_LIMIT
        )
        assert result.fingerprint_result.records == ()

    def test_accepts_analysis_at_its_exact_budget(self, tmp_path):
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
        path = tmp_path / "sphere.3mf"
        path.write_bytes(three_mf(meshes={1: mesh}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True, triangle_cap=320)
        )

        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert result.fingerprint_result.records[0].values["face_count"] == 320
        assert isinstance(result.geometry_outcome, GeometryReady)

    @pytest.mark.parametrize("sampled", [False, True])
    def test_releases_analysis_buffers_after_streaming(
        self, cube, monkeypatch, sampled
    ):
        references = []
        lifetimes = []
        reclaims = []
        reclaim = mesh_policy.reclaim_memory

        def observe_reclaim():
            reclaims.append(True)
            reclaim()

        monkeypatch.setattr(mesh_policy, "reclaim_memory", observe_reclaim)
        extract = thumbnail_engine.extract
        stream = thumbnail_engine.stl_streaming.render_stl_preview_isolated

        def observe_extract(prepared):
            references.extend(
                (
                    weakref.ref(prepared),
                    weakref.ref(prepared.whole_mesh),
                    weakref.ref(prepared.whole_mesh.vertices),
                    weakref.ref(prepared.whole_mesh.faces),
                )
            )
            references.extend(
                weakref.ref(array)
                for resource in prepared.scene.resources
                for array in (resource.vertices, resource.faces)
            )
            return extract(prepared)

        def observe_stream(*args, **kwargs):
            assert reclaims == ([True] if not sampled else [])
            assert references == []
            lifetimes.append("streaming")
            return stream(*args, **kwargs)

        monkeypatch.setattr(thumbnail_engine, "extract", observe_extract)
        monkeypatch.setattr(
            thumbnail_engine.mesh_render,
            "render_mesh_thumbnail",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            thumbnail_engine.stl_streaming,
            "render_stl_preview_isolated",
            observe_stream,
        )
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *args, **kwargs: sampled)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, width=64, include_fingerprint=True)
        )

        assert result.image is not None
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.fingerprint_result.state is (
            FingerprintResultState.PARTIAL if sampled else FingerprintResultState.READY
        )
        assert references
        assert lifetimes == ["streaming"]
        assert all(reference() is None for reference in references)
        assert reclaims == ([True, True] if not sampled else [True])

    def test_analysis_budget_is_independent_of_scene_render_budget(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "plate-fingerprint.3mf"
        placements = tuple(
            (1, f"1 0 0 0 1 0 0 0 1 {index * 20} 0 0") for index in range(64)
        )
        path.write_bytes(three_mf(build=placements))
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        calls = []
        materialize = mesh_resources.materialize_scene

        def construct(scene):
            calls.append(True)
            return materialize(scene)

        monkeypatch.setattr(mesh_resources, "materialize_scene", construct)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True, triangle_cap=300)
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 256
        assert isinstance(result.volume, VolumeMeasured)
        assert result.image is None
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert result.fingerprint_result.records
        assert calls == [True]

    @pytest.mark.parametrize("error", [ValueError, MemoryError])
    def test_releases_failed_sample_buffers_after_streaming(
        self, cube, monkeypatch, error
    ):
        references = []
        lifetimes = []
        reclaims = []
        reclaim = mesh_policy.reclaim_memory

        def observe_reclaim():
            reclaims.append(True)
            reclaim()

        monkeypatch.setattr(mesh_policy, "reclaim_memory", observe_reclaim)
        stream = thumbnail_engine.stl_streaming.render_stl_preview_isolated

        def fail_construction(*, vertices, faces, process):
            references.extend((weakref.ref(vertices), weakref.ref(faces)))
            raise error("sample construction failed")

        def observe_stream(*args, **kwargs):
            assert reclaims == ([])
            assert references == []
            lifetimes.append("streaming")
            return stream(*args, **kwargs)

        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *args, **kwargs: True)
        monkeypatch.setattr(trimesh, "Trimesh", fail_construction)
        monkeypatch.setattr(
            thumbnail_engine.stl_streaming,
            "render_stl_preview_isolated",
            observe_stream,
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, width=64, include_fingerprint=True)
        )

        assert result.image is not None
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.fingerprint_result.failure_code == "analysis_failed"
        assert references
        assert lifetimes == ["streaming"]
        assert all(reference() is None for reference in references)
        assert reclaims == ([True])

    def test_releases_failed_preparation_buffers_after_streaming(
        self, cube, monkeypatch
    ):
        references = []
        lifetimes = []
        reclaims = []
        reclaim = mesh_policy.reclaim_memory

        def observe_reclaim():
            reclaims.append(True)
            reclaim()

        monkeypatch.setattr(mesh_policy, "reclaim_memory", observe_reclaim)
        prepare = thumbnail_engine.prepare_loaded_mesh
        stream = thumbnail_engine.stl_streaming.render_stl_preview_isolated

        def fail_preparation(*args, **kwargs):
            prepared = prepare(*args, **kwargs)
            references.extend((weakref.ref(prepared), weakref.ref(prepared.whole_mesh)))
            references.extend(
                weakref.ref(array)
                for resource in prepared.scene.resources
                for array in (resource.vertices, resource.faces)
            )
            raise ValueError("preparation failed")

        def observe_stream(*args, **kwargs):
            assert reclaims == ([True])
            assert references == []
            lifetimes.append("streaming")
            return stream(*args, **kwargs)

        monkeypatch.setattr(thumbnail_engine, "prepare_loaded_mesh", fail_preparation)
        monkeypatch.setattr(
            thumbnail_engine.mesh_render,
            "render_mesh_thumbnail",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            thumbnail_engine.stl_streaming,
            "render_stl_preview_isolated",
            observe_stream,
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, width=64, include_fingerprint=True)
        )

        assert result.image is not None
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.fingerprint_result.failure_code == "analysis_failed"
        assert references
        assert lifetimes == ["streaming"]
        assert all(reference() is None for reference in references)
        assert reclaims == ([True, True])


class TestUnreferencedVertices:
    def test_preserves_3mf_preview(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        padded = trimesh.Trimesh(
            vertices=np.concatenate([mesh.vertices, [[1e6, -1e6, 1e6]]]),
            faces=mesh.faces.copy(),
            process=False,
        )
        original_path = tmp_path / "original.3mf"
        padded_path = tmp_path / "padded.3mf"
        original_path.write_bytes(three_mf(meshes={1: mesh}))
        padded_path.write_bytes(three_mf(meshes={1: padded}))

        original = ThumbnailEngine().generate(
            ThumbnailRequest(original_path, width=128, height=128)
        )
        result = ThumbnailEngine().generate(
            ThumbnailRequest(padded_path, width=128, height=128)
        )

        assert original.image is not None and result.image is not None
        expected = np.asarray(Image.open(io.BytesIO(original.image)))
        actual = np.asarray(Image.open(io.BytesIO(result.image)))
        assert np.count_nonzero(expected[..., 3]) > 0
        np.testing.assert_array_equal(actual, expected)

    def test_preserves_3mf_metadata(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.vertices = np.concatenate([mesh.vertices, [[1e6, -1e6, 1e6]]])
        path = tmp_path / "padded.3mf"
        path.write_bytes(three_mf(meshes={1: mesh}))

        result = ThumbnailEngine().generate(ThumbnailRequest(path, width=64, height=64))

        assert result.geometry == pytest.approx(
            {
                "bbox_x_mm": 10,
                "bbox_y_mm": 10,
                "bbox_z_mm": 10,
                "volume_mm3": 1000,
                "triangle_count": 12,
            }
        )


class TestFallbackMeasurements:
    def test_keeps_global_bounds_when_remote_facet_is_not_retained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.media import stl_fallback, stl_streaming

        source = tmp_path / "partial-preview.stl"
        source.write_bytes(
            content.binary_stl_facets(
                [
                    ((0, 0, 0), (2, 0, 1), (0, 2, 1)),
                    ((100, 3, 4), (101, 3, 4), (100, 4, 5)),
                ]
            )
        )
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        monkeypatch.setattr(
            stl_streaming, "render_stl_preview_isolated", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(stl_fallback, "_MAX_SAMPLED_TRIANGLES", 1)
        monkeypatch.setenv(WORKER_MARKER, str(os.getpid()))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, width=128, height=128)
        )

        assert result.image is not None
        assert result.coverage.preview is PreviewCoverage.PARTIAL
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "bbox_x_mm": 101.0,
            "bbox_y_mm": 4.0,
            "bbox_z_mm": 5.0,
            "triangle_count": 2,
            "volume_mm3": None,
        }


class TestVolumeRequestOwnership:
    @pytest.mark.parametrize(
        "strategy", [ThumbnailStrategy.STREAMING, ThumbnailStrategy.FALLBACK], ids=str
    )
    def test_thumbnail_only_preview_preserves_unrequested_volume(
        self, tmp_path, monkeypatch, strategy
    ):
        from printstash_core.mesh.measurements import (
            VolumeNotCalculated,
            VolumeNotCalculatedCause,
        )

        from app.modules.media import stl_streaming
        from app.modules.media.mesh_contracts import GeometryNotRequested

        mesh = trimesh.creation.box(extents=[10, 10, 10])
        source = tmp_path / "thumbnail-only.stl"
        source.write_bytes(mesh.export(file_type="stl"))
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        preview = {
            ThumbnailStrategy.STREAMING: stl_streaming.render_stl_preview_isolated,
            ThumbnailStrategy.FALLBACK: lambda *a, **kw: None,
        }[strategy]
        monkeypatch.setattr(stl_streaming, "render_stl_preview_isolated", preview)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_geometry=False, width=32, height=32)
        )

        assert result.image is not None
        assert result.strategy is strategy
        assert result.geometry_outcome == GeometryNotRequested()
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.NOT_REQUESTED
        )


class TestMeasurementNumericalValidity:
    @pytest.mark.parametrize("suffix", ["stl", "3mf"])
    def test_refuses_nonfinite_extents_from_finite_source_coordinates(
        self, tmp_path, monkeypatch, suffix
    ):
        from printstash_core.mesh.measurements import (
            VolumeNotCalculated,
            VolumeNotCalculatedCause,
        )

        vertices = [[-1e308, 0, 0], [1e308, 0, 0], [0, 1, 0], [0, 0, 1]]
        faces = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        facets = [tuple(tuple(vertices[index]) for index in face) for face in faces]
        raw = {
            "stl": content.ascii_stl_facets(facets),
            "3mf": three_mf(meshes={1: mesh}),
        }[suffix]
        source = tmp_path / ("extent-overflow." + suffix)
        source.write_bytes(raw)
        monkeypatch.setenv("VAULT_MESH_MEMORY_BUDGET_FRACTION", "0")
        assert np.isfinite(mesh.vertices).all()
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                source,
                include_geometry=True,
                include_thumbnail=False,
                include_fingerprint=False,
            )
        )
        assert result.geometry_outcome == GeometryRefused(
            ThumbnailFailureReason.INVALID_SOURCE
        )
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        )
        assert result.geometry == {
            "bbox_x_mm": None,
            "bbox_y_mm": None,
            "bbox_z_mm": None,
            "volume_mm3": None,
            "triangle_count": None,
        }
        assert (
            json.loads(json.dumps(result.geometry, allow_nan=False)) == result.geometry
        )


class TestVolumeTranslationPrecision:
    @pytest.mark.parametrize("offset", [0.0, 1e15, -1e15])
    def test_preserves_integral_for_large_translated_3mf(self, tmp_path, offset):
        from printstash_core.mesh.measurements import VolumeMeasured

        mesh = trimesh.creation.box(extents=[1, 2, 3])
        mesh.apply_translation([offset, offset, offset])
        vertices = mesh.vertices.copy()
        source = tmp_path / "translated-volume.3mf"
        source.write_bytes(three_mf(meshes={1: mesh}))
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False, include_fingerprint=False)
        )
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry["bbox_x_mm"] == 1.0
        assert result.geometry["bbox_y_mm"] == 2.0
        assert result.geometry["bbox_z_mm"] == 3.0
        assert result.volume == VolumeMeasured(6.0)
        np.testing.assert_array_equal(mesh.vertices, vertices)

    @pytest.mark.parametrize("offset", [1e15, -1e15])
    def test_retains_negative_orientation_after_translation(self, tmp_path, offset):
        from printstash_core.mesh.measurements import (
            VolumeUnavailable,
            VolumeUnavailableCause,
        )

        mesh = trimesh.creation.box(extents=[1, 2, 3])
        mesh.vertices[:, 0] *= -1
        mesh.apply_translation([offset, offset, offset])
        faces = mesh.faces.copy()
        vertices = mesh.vertices.copy()
        source = tmp_path / "reflected-volume.3mf"
        source.write_bytes(three_mf(meshes={1: mesh}))
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False, include_fingerprint=False)
        )
        assert result.geometry_outcome == GeometryReady()
        assert result.volume == VolumeUnavailable(
            VolumeUnavailableCause.NON_POSITIVE_INTEGRAL
        )
        assert result.geometry["volume_mm3"] is None
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, faces)


class TestDisconnectedVolumePrecision:
    def test_preserves_far_separated_3mf_instance_integrals(self, tmp_path):
        from printstash_core.mesh.measurements import VolumeMeasured

        mesh = trimesh.creation.box(extents=[1, 2, 3])
        source = tmp_path / "separated-instances.3mf"
        source.write_bytes(
            three_mf(
                meshes={1: mesh},
                build=(
                    (
                        1,
                        "1 0 0 0 1 0 0 0 1 1000000000000000 1000000000000000 1000000000000000",
                    ),
                    (
                        1,
                        "1 0 0 0 1 0 0 0 1 -1000000000000000 -1000000000000000 -1000000000000000",
                    ),
                ),
            )
        )
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False, include_fingerprint=False)
        )
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry["triangle_count"] == 24
        assert result.volume == VolumeMeasured(12.0)

    def test_retains_negative_inner_shell_contribution(self, tmp_path):
        from printstash_core.mesh.measurements import VolumeMeasured

        outer = trimesh.creation.box(extents=[1, 2, 3])
        inner = trimesh.creation.box(extents=[0.5, 1, 2])
        inner.invert()
        outer.apply_translation([1e15, 1e15, 1e15])
        inner.apply_translation([1e15, 1e15, 1e15])
        source = tmp_path / "cavity.3mf"
        source.write_bytes(
            three_mf(meshes={1: outer, 2: inner}, build=((1, None), (2, None)))
        )
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False, include_fingerprint=False)
        )
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry["triangle_count"] == 24
        assert result.volume == VolumeMeasured(5.0)


class TestBoundedVolumeAuthority:
    @pytest.mark.parametrize(
        "strategy", [ThumbnailStrategy.STREAMING, ThumbnailStrategy.FALLBACK], ids=str
    )
    def test_complete_bounded_preview_exposes_unassessed_topology(
        self, tmp_path, monkeypatch, strategy
    ):
        from app.modules.media import stl_streaming

        source = tmp_path / "complete.stl"
        source.write_bytes(
            trimesh.creation.box(extents=[1, 2, 3]).export(file_type="stl")
        )
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        preview = {
            ThumbnailStrategy.STREAMING: stl_streaming.render_stl_preview_isolated,
            ThumbnailStrategy.FALLBACK: lambda *a, **kw: None,
        }[strategy]
        monkeypatch.setattr(stl_streaming, "render_stl_preview_isolated", preview)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, width=32, height=32, include_fingerprint=False)
        )
        assert result.strategy is strategy
        assert result.image is not None
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry == {
            "bbox_x_mm": 1.0,
            "bbox_y_mm": 2.0,
            "bbox_z_mm": 3.0,
            "volume_mm3": None,
            "triangle_count": 12,
        }
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )

    def test_complete_source_with_partial_preview_preserves_unknown_volume(
        self, tmp_path, monkeypatch
    ):
        from app.modules.media import stl_fallback, stl_streaming

        source = tmp_path / "partial.stl"
        source.write_bytes(
            trimesh.creation.box(extents=[1, 2, 3]).export(file_type="stl")
        )
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        monkeypatch.setattr(
            stl_streaming, "render_stl_preview_isolated", lambda *a, **kw: None
        )
        original_fallback = stl_fallback.render_stl_thumbnail
        monkeypatch.setattr(
            stl_fallback,
            "render_stl_thumbnail",
            lambda path, **kw: original_fallback(path, max_triangles=4, **kw),
        )
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, width=32, height=32, include_fingerprint=False)
        )
        assert result.strategy is ThumbnailStrategy.FALLBACK
        assert result.image is not None
        assert result.coverage.preview is PreviewCoverage.PARTIAL
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.geometry_outcome == GeometryReady()
        assert result.geometry == {
            "bbox_x_mm": 1.0,
            "bbox_y_mm": 2.0,
            "bbox_z_mm": 3.0,
            "volume_mm3": None,
            "triangle_count": 12,
        }
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )


class TestCoverage:
    def test_embedded_preview_does_not_certify_unread_source(self, tmp_path):
        source = tmp_path / "embedded.3mf"
        source.write_bytes(three_mf(extras={"Metadata/thumbnail.png": content.png()}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_geometry=False)
        )

        assert result.image is not None
        assert result.coverage == MeshCoverage(
            SourceScanState.NOT_SCANNED,
            GeometryNotLoaded(),
            PreviewCoverage.DOCUMENT_SUPPLIED,
        )

    def test_complete_source_scan_survives_partial_preview(self, tmp_path, monkeypatch):
        from app.modules.media import mesh_render, stl_fallback, stl_streaming

        source = tmp_path / "partial-preview.stl"
        source.write_bytes(
            trimesh.creation.box(extents=[10, 20, 30]).export(file_type="stl")
        )
        monkeypatch.setattr(
            mesh_render, "render_mesh_thumbnail", lambda *args, **kwargs: None
        )
        monkeypatch.setattr(
            stl_streaming, "render_stl_preview_isolated", lambda *args, **kwargs: None
        )
        render_fallback = stl_fallback.render_stl_thumbnail
        monkeypatch.setattr(
            stl_fallback,
            "render_stl_thumbnail",
            lambda path, **kwargs: render_fallback(path, max_triangles=4, **kwargs),
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, width=128, height=128, include_fingerprint=False)
        )

        assert result.image is not None
        assert result.coverage == MeshCoverage(
            SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.PARTIAL
        )
        assert result.geometry["triangle_count"] == 12

    def test_complete_source_scan_survives_sampled_analysis(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "sampled-analysis.stl"
        source.write_bytes(
            trimesh.creation.icosphere(subdivisions=2, radius=5).export(file_type="stl")
        )
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_thumbnail=False, include_fingerprint=True)
        )

        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, SampledGeometry)
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert result.geometry["triangle_count"] == 320
        assert result.fingerprint_result.state is FingerprintResultState.PARTIAL

    def test_analysis_only_preserves_complete_source_scan(self, tmp_path, monkeypatch):
        source = tmp_path / "analysis-only.stl"
        source.write_bytes(
            trimesh.creation.icosphere(subdivisions=2, radius=5).export(file_type="stl")
        )
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                source,
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, SampledGeometry)
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert result.fingerprint_result.state is FingerprintResultState.PARTIAL
        assert all(value is None for value in result.geometry.values())


class TestUnsupported3MFCapability:
    @pytest.mark.parametrize("embedded", [False, True])
    def test_refuses_required_capability_without_losing_document_preview(
        self, tmp_path, embedded
    ):
        preview = content.png()
        original = three_mf(
            extras={"Metadata/thumbnail.png": preview} if embedded else None
        )
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["3D/3dmodel.model"] = entries["3D/3dmodel.model"].replace(
            b"<model ",
            b'<model xmlns:future="urn:printstash:test:unsupported" requiredextensions="future" ',
            1,
        )
        source = tmp_path / "required.3mf"
        source.write_bytes(content.zip_bytes(entries))
        before = source.read_bytes()

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_fingerprint=True)
        )

        assert result.geometry_outcome == GeometryRefused(
            ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
        )
        assert result.geometry["triangle_count"] is None
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        )
        assert result.fingerprint_result.state is FingerprintResultState.UNSUPPORTED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.UNSUPPORTED_3MF_CAPABILITY
        )
        assert result.fingerprint_result.records == ()
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert source.read_bytes() == before
        if embedded:
            assert result.image == preview
            assert result.strategy is ThumbnailStrategy.EMBEDDED
            assert result.coverage.preview is PreviewCoverage.DOCUMENT_SUPPLIED
        else:
            assert result.image is None
            assert (
                result.failure_reason is ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
            )


class TestRetainedThreeMFScene:
    @pytest.mark.parametrize("fingerprint", [False, True])
    @pytest.mark.parametrize("thumbnail", [False, True])
    def test_preserves_basic_outputs_without_source_materialization(
        self, tmp_path, monkeypatch, fingerprint, thumbnail
    ):
        path = tmp_path / "plate.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 64))

        def materialize(*_args, **_kwargs):
            raise AssertionError("basic outputs must not flatten shared geometry")

        monkeypatch.setattr(mesh_resources, "materialize_scene", materialize)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=thumbnail,
                include_fingerprint=fingerprint,
                triangle_cap=100,
                width=160,
                height=120,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "volume_mm3": 64000.0,
            "triangle_count": 256,
        }
        assert (result.image is not None) is thumbnail
        assert result.failure_reason is None
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.coverage.preview is (
            PreviewCoverage.COMPLETE if thumbnail else PreviewCoverage.NOT_PRODUCED
        )
        assert result.volume == VolumeMeasured(64000.0)
        assert (result.fingerprint_result is not None) is fingerprint
        if fingerprint:
            assert result.fingerprint_result is not None
            assert result.fingerprint_result.state is FingerprintResultState.FAILED
            assert (
                result.fingerprint_result.failure_code
                is FingerprintFailureCode.GEOMETRY_WORK_LIMIT
            )
            assert result.fingerprint_result.records == ()

    def test_materializes_eligible_fingerprint_once(self, tmp_path, monkeypatch):
        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        calls = []
        materialize = mesh_resources.materialize_scene

        def construct(scene):
            calls.append(sum(len(r.faces) for r in scene.resources))
            return materialize(scene)

        monkeypatch.setattr(mesh_resources, "materialize_scene", construct)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=True,
            )
        )

        assert calls == [4]
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["volume_mm3"] == 1000.0
        assert result.volume == VolumeMeasured(1000.0)
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert result.image is not None

    @pytest.mark.parametrize("fingerprint", [False, True])
    @pytest.mark.parametrize("thumbnail", [False, True])
    def test_preserves_volume_when_halves_close_after_whole_scene_welding(
        self, tmp_path, monkeypatch, fingerprint, thumbnail
    ):
        tetra = tetrahedron()
        half_a = trimesh.Trimesh(
            vertices=tetra.vertices, faces=tetra.faces[:2], process=False
        )
        half_b = trimesh.Trimesh(
            vertices=tetra.vertices, faces=tetra.faces[2:], process=False
        )
        path = tmp_path / "halves.3mf"
        path.write_bytes(
            three_mf(meshes={1: half_a, 2: half_b}, build=((1, None), (2, None)))
        )

        baseline = ThumbnailEngine().generate(
            ThumbnailRequest(
                path, include_thumbnail=thumbnail, include_fingerprint=fingerprint
            )
        )
        calls = []
        mesh_refs = []
        detached_refs = []
        materialize = mesh_resources.materialize_scene
        detach = getattr(mesh_resources, "detach_scene_mesh", None)
        extract = thumbnail_engine.extract
        render = thumbnail_engine.mesh_render.render_scene_thumbnail

        def retain(prepared, **kwargs):
            assert detach is not None
            detached = detach(prepared, **kwargs)
            detached_refs.extend(
                (weakref.ref(detached.vertices), weakref.ref(detached.faces))
            )
            assert np.shares_memory(detached.vertices, prepared.whole_mesh.vertices)
            assert np.shares_memory(detached.faces, prepared.whole_mesh.faces)
            return detached

        def preview(*args, **kwargs):
            assert calls == [True]
            assert all(reference() is None for reference in mesh_refs)
            if fingerprint:
                assert len(detached_refs) == 2
                assert all(reference() is not None for reference in detached_refs)
            else:
                assert detached_refs == []
            return render(*args, **kwargs)

        def fingerprint_mesh(prepared):
            if thumbnail:
                assert len(detached_refs) == 2
                assert np.shares_memory(
                    prepared.whole_mesh.vertices, detached_refs[0]()
                )
                assert np.shares_memory(prepared.whole_mesh.faces, detached_refs[1]())
            return extract(prepared)

        def construct(scene):
            calls.append(True)
            prepared = materialize(scene)
            mesh_refs.append(weakref.ref(prepared.whole_mesh))
            return prepared

        monkeypatch.setattr(mesh_resources, "materialize_scene", construct)
        monkeypatch.setattr(mesh_resources, "detach_scene_mesh", retain, raising=False)
        monkeypatch.setattr(
            thumbnail_engine.mesh_render, "render_scene_thumbnail", preview
        )
        monkeypatch.setattr(thumbnail_engine, "extract", fingerprint_mesh)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=thumbnail,
                include_fingerprint=fingerprint,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "volume_mm3": 1000.0,
            "triangle_count": 4,
        }
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert result.volume == VolumeMeasured(1000.0)
        assert isinstance(result.coverage.geometry, CompleteGeometry)
        assert calls == [True]
        assert result.image == baseline.image
        assert result.fingerprint_result == baseline.fingerprint_result
        assert all(reference() is None for reference in mesh_refs)
        assert all(reference() is None for reference in detached_refs)
        if fingerprint:
            assert result.fingerprint_result is not None
            assert result.fingerprint_result.state is FingerprintResultState.READY

    @pytest.mark.parametrize("fingerprint", [False, True])
    def test_preserves_scene_facts_above_topology_budget(
        self, tmp_path, monkeypatch, fingerprint
    ):
        tetra = tetrahedron()
        halves = {
            1: trimesh.Trimesh(
                vertices=tetra.vertices, faces=tetra.faces[:2], process=False
            ),
            2: trimesh.Trimesh(
                vertices=tetra.vertices, faces=tetra.faces[2:], process=False
            ),
        }
        path = tmp_path / "large-topology.3mf"
        path.write_bytes(three_mf(meshes=halves, build=((1, None), (2, None)) * 64))
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)

        def materialize(*_args, **_kwargs):
            raise AssertionError(
                "topology over its allocation budget must stay retained"
            )

        monkeypatch.setattr(mesh_resources, "materialize_scene", materialize)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_thumbnail=False,
                include_fingerprint=fingerprint,
                triangle_cap=100,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 256,
            "volume_mm3": None,
        }
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.failure_reason is None
        if fingerprint:
            assert result.fingerprint_result is not None
            assert result.fingerprint_result.state is FingerprintResultState.FAILED
            assert (
                result.fingerprint_result.failure_code
                is FingerprintFailureCode.GEOMETRY_WORK_LIMIT
            )
        else:
            assert result.fingerprint_result is None

    def test_refuses_volume_for_coincident_unwelded_copies(self, tmp_path):
        tetra = tetrahedron()
        unwelded = trimesh.Trimesh(
            vertices=tetra.vertices[tetra.faces].reshape((-1, 3)),
            faces=np.arange(12).reshape((-1, 3)),
            process=False,
        )
        path = tmp_path / "coincident.3mf"
        path.write_bytes(three_mf(meshes={1: unwelded}, build=((1, None), (1, None))))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=False,
                include_fingerprint=False,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 8
        assert result.geometry["volume_mm3"] is None
        assert result.volume == VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT)
        assert result.coverage.source_scan is SourceScanState.COMPLETE

    def test_refuses_optional_fingerprint_before_source_materialization(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "plate-fingerprint.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 64))

        def materialize(*_args, **_kwargs):
            raise AssertionError("refused fingerprint must not flatten shared geometry")

        monkeypatch.setattr(mesh_resources, "materialize_scene", materialize)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=True,
                triangle_cap=100,
            )
        )

        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.GEOMETRY_WORK_LIMIT
        )
        assert result.fingerprint_result.records == ()
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.image is not None

    @pytest.mark.parametrize(
        "include_fingerprint",
        [
            pytest.param(False, id="basic-outputs"),
            pytest.param(True, id="with-fingerprint"),
        ],
    )
    def test_releases_analysis_buffers_in_stage_order(
        self, tmp_path, monkeypatch, include_fingerprint
    ):
        from app.modules.media import mesh_render

        path = tmp_path / "lifetime.3mf"
        path.write_bytes(three_mf())
        meshes = []
        stages = []
        extract = thumbnail_engine.extract
        constructor = trimesh.Trimesh.__init__
        render = mesh_render.render_scene_thumbnail

        def construct(mesh, *args, **kwargs):
            constructor(mesh, *args, **kwargs)
            meshes.append(weakref.ref(mesh))

        def check_scene(scene, *args, **kwargs):
            assert len(meshes) == 1
            assert all(reference() is None for reference in meshes)
            stages.append("render")
            return render(scene, *args, **kwargs)

        def check_fingerprint(prepared):
            assert stages == ["render"]
            stages.append("fingerprint")
            return extract(prepared)

        monkeypatch.setattr(trimesh.Trimesh, "__init__", construct)
        monkeypatch.setattr(mesh_render, "render_scene_thumbnail", check_scene)
        monkeypatch.setattr(thumbnail_engine, "extract", check_fingerprint)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=include_fingerprint,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert (result.fingerprint_result is not None) is include_fingerprint
        assert (
            result.fingerprint_result is None
            or result.fingerprint_result.state is FingerprintResultState.READY
        )
        assert result.geometry["volume_mm3"] == 1000.0
        assert result.volume == VolumeMeasured(1000.0)
        assert result.image is not None
        assert stages == ["render"] + (["fingerprint"] if include_fingerprint else [])
        assert len(meshes) >= 1 + int(include_fingerprint)
        assert all(reference() is None for reference in meshes)

    @pytest.mark.parametrize("fingerprint", [False, True])
    def test_refuses_transformed_numeric_overflow_without_worker_crash(
        self, tmp_path, fingerprint
    ):
        path = tmp_path / "range.3mf"
        path.write_bytes(three_mf(build=((1, "1e308 0 0 0 1e308 0 0 0 1e308 0 0 0"),)))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=False,
                include_fingerprint=fingerprint,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryRefused)
        assert result.geometry_outcome.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert result.geometry["triangle_count"] is None
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert (result.fingerprint_result is not None) is fingerprint
        if fingerprint:
            assert result.fingerprint_result is not None
            assert result.fingerprint_result.state is FingerprintResultState.FAILED
            assert (
                result.fingerprint_result.failure_code
                is FingerprintFailureCode.NUMERIC_RANGE
            )
            assert result.fingerprint_result.records == ()

    def test_keeps_embedded_preview_when_measurements_exceed_numeric_range(
        self, tmp_path
    ):
        path = tmp_path / "range-preview.3mf"
        path.write_bytes(
            three_mf(
                build=((1, "1e308 0 0 0 1e308 0 0 0 1e308 0 0 0"),),
                extras={"Metadata/thumbnail.png": content.png()},
            )
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=True,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryRefused)
        assert result.geometry_outcome.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.NUMERIC_RANGE
        )
        assert result.image is not None
        assert result.strategy is ThumbnailStrategy.EMBEDDED
        assert result.failure_reason is None

    def test_preserves_known_geometry_when_topology_materialization_fails(
        self, tmp_path, monkeypatch
    ):
        tetra = tetrahedron()
        half_a = trimesh.Trimesh(
            vertices=tetra.vertices, faces=tetra.faces[:2], process=False
        )
        half_b = trimesh.Trimesh(
            vertices=tetra.vertices, faces=tetra.faces[2:], process=False
        )
        path = tmp_path / "topology.3mf"
        path.write_bytes(
            three_mf(meshes={1: half_a, 2: half_b}, build=((1, None), (2, None)))
        )

        calls = []

        def fail(_scene):
            calls.append(True)
            raise GeometryError("invalid_3mf")

        monkeypatch.setattr(mesh_resources, "materialize_scene", fail)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=True,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": None,
        }
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code is FingerprintFailureCode.INVALID_3MF
        )
        assert calls == [True]
        assert result.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED
        )
        assert result.image is not None
        assert result.failure_reason is None

    def test_refuses_nonfinite_extent_from_finite_placements(self, tmp_path):
        path = tmp_path / "extent.3mf"
        path.write_bytes(
            three_mf(
                build=(
                    (1, "1 0 0 0 1 0 0 0 1 -1e308 0 0"),
                    (1, "1 0 0 0 1 0 0 0 1 1e308 0 0"),
                )
            )
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=True,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        assert isinstance(result.geometry_outcome, GeometryRefused)
        assert result.geometry_outcome.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert result.geometry["bbox_x_mm"] is None
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.NUMERIC_RANGE
        )


class TestThumbnailFailureBoundaries:
    def test_missing_source_returns_typed_refusals(self, tmp_path):
        source = tmp_path / "missing.stl"
        outputs = []
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_fingerprint=True), on_output=outputs.append
        )
        assert not source.exists()
        assert result.image is None
        assert isinstance(result.geometry_outcome, GeometryRefused)
        assert result.geometry_outcome.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert result.geometry["triangle_count"] is None
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.SOURCE_UNAVAILABLE
        )
        assert len(outputs) == 2
        assert result.coverage.source_scan is SourceScanState.NOT_SCANNED

    @pytest.mark.parametrize(
        "embedded", [False, True], ids=["without-preview", "with-preview"]
    )
    def test_allocator_refusal_preserves_document_preview(
        self, tmp_path, monkeypatch, embedded
    ):
        source = tmp_path / "allocation.3mf"
        source_bytes = three_mf(
            extras={"Metadata/thumbnail.png": content.png()} if embedded else {}
        )
        source.write_bytes(source_bytes)

        def unavailable(*args, **kwargs):
            raise MemoryError("external NumPy allocator unavailable")

        monkeypatch.setattr(np, "array", unavailable)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_fingerprint=True)
        )
        assert isinstance(result.geometry_outcome, GeometryRefused)
        assert result.geometry_outcome.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert result.geometry["triangle_count"] is None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.RESOURCE_LIMIT
        )
        assert source.read_bytes() == source_bytes
        if embedded:
            assert result.image == content.png()
            assert result.strategy is ThumbnailStrategy.EMBEDDED
            assert result.coverage.preview is PreviewCoverage.DOCUMENT_SUPPLIED
            assert result.failure_reason is None
        else:
            assert result.image is None
            assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT

    @pytest.mark.parametrize(
        "error", [OSError, ValueError], ids=["oserror", "valueerror"]
    )
    def test_unavailable_rss_does_not_discard_real_outputs(
        self, cube, monkeypatch, error
    ):
        outputs = []
        original = cube.read_bytes()

        def unavailable(*args, **kwargs):
            raise error("external resource counter unavailable")

        monkeypatch.setattr(thumbnail_engine.resource, "getrusage", unavailable)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, width=64, height=64), on_output=outputs.append
        )
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 12
        assert result.image is not None
        assert result.failure_reason is None
        assert result.peak_rss_bytes is None
        assert len(outputs) == 2
        assert all(output.peak_rss_bytes is None for output in outputs)
        assert cube.read_bytes() == original

    @pytest.mark.parametrize(
        "error", [RuntimeError, MemoryError], ids=["runtimeerror", "memoryerror"]
    )
    def test_propagates_publication_failure_after_allocator_refusal(
        self, tmp_path, monkeypatch, error
    ):
        source = tmp_path / "allocation.3mf"
        source_bytes = three_mf()
        source.write_bytes(source_bytes)
        fault = error("output publication refused")
        outputs = []

        def unavailable(*args, **kwargs):
            raise MemoryError("external NumPy allocator unavailable")

        def refuse(output):
            outputs.append(output)
            raise fault

        monkeypatch.setattr(np, "array", unavailable)
        with pytest.raises(error) as raised:
            ThumbnailEngine().generate(ThumbnailRequest(source), on_output=refuse)
        assert raised.value is fault
        assert len(outputs) == 1
        assert isinstance(outputs[0].outcome, GeometryRefused)
        assert outputs[0].outcome.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert source.read_bytes() == source_bytes
