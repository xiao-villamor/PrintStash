"""Requested basic outputs are observable before later expensive mesh work."""

import pytest
from printstash_core.mesh.measurements import VolumeMeasured

from app.modules.media import mesh_render, thumbnail_engine
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    PreviewCoverage,
    SourceScanState,
    ThumbnailRequest,
)
from app.modules.media.mesh_facts import FingerprintFailureCode, FingerprintResultState
from app.modules.media.thumbnail_engine import ThumbnailEngine
from tests.factories.geometry import three_mf


class TestGenerate:
    def test_emits_geometry_before_render(self, tmp_path, monkeypatch):
        from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput

        path = tmp_path / "part.3mf"
        source = three_mf()
        path.write_bytes(source)
        outputs = []
        render = mesh_render.render_scene_thumbnail

        def verify_geometry(scene, *args, **kwargs):
            assert len(outputs) == 1
            geometry = outputs[0]
            assert isinstance(geometry, GeometryOutput)
            assert geometry.outcome == GeometryReady()
            assert geometry.geometry["volume_mm3"] == 1000.0
            assert geometry.volume == VolumeMeasured(1000.0)
            assert geometry.coverage.source_scan is SourceScanState.COMPLETE
            assert isinstance(geometry.coverage.geometry, GeometryNotLoaded)
            assert geometry.coverage.preview is PreviewCoverage.NOT_PRODUCED
            return render(scene, *args, **kwargs)

        monkeypatch.setattr(mesh_render, "render_scene_thumbnail", verify_geometry)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=False), on_output=outputs.append
        )

        assert len(outputs) == 2
        assert isinstance(outputs[1], ThumbnailOutput)
        assert outputs[1].image == result.image
        assert result.volume == outputs[0].volume
        assert path.read_bytes() == source

    def test_emits_thumbnail_before_fingerprint(self, tmp_path, monkeypatch):
        from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput

        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        outputs = []
        extract = thumbnail_engine.extract

        def verify_thumbnail(prepared):
            assert tuple(type(output) for output in outputs) == (
                GeometryOutput,
                ThumbnailOutput,
            )
            assert outputs[1].image is not None
            assert outputs[1].coverage.preview is PreviewCoverage.COMPLETE
            return extract(prepared)

        monkeypatch.setattr(thumbnail_engine, "extract", verify_thumbnail)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True), on_output=outputs.append
        )

        assert len(outputs) == 2
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.records
        assert outputs[1].image == result.image
        assert outputs[0].volume == result.volume == VolumeMeasured(1000.0)

    @pytest.mark.parametrize("geometry", [False, True])
    @pytest.mark.parametrize("thumbnail", [False, True])
    @pytest.mark.parametrize("fingerprint", [False, True])
    def test_emits_requested_outputs_once(
        self, tmp_path, geometry, thumbnail, fingerprint
    ):
        from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput

        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        outputs = []
        expected = (() if not geometry else (GeometryOutput,)) + (
            () if not thumbnail else (ThumbnailOutput,)
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_geometry=geometry,
                include_thumbnail=thumbnail,
                include_fingerprint=fingerprint,
            ),
            on_output=outputs.append,
        )

        assert tuple(type(output) for output in outputs) == expected
        assert (result.image is not None) is thumbnail
        assert (result.fingerprint_result is not None) is fingerprint

    def test_keeps_emitted_geometry_when_renderer_raises(self, tmp_path, monkeypatch):
        from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput

        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        outputs = []

        def broken(*args, **kwargs):
            assert len(outputs) == 1
            assert isinstance(outputs[0], GeometryOutput)
            raise RuntimeError("render failed after metadata")

        monkeypatch.setattr(mesh_render, "render_scene_thumbnail", broken)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path), on_output=outputs.append
        )

        assert isinstance(outputs[1], ThumbnailOutput)
        assert outputs[0].outcome == result.geometry_outcome == GeometryReady()
        assert outputs[0].volume == result.volume == VolumeMeasured(1000.0)
        assert outputs[1].image is None
        assert result.image is None

    def test_keeps_emitted_thumbnail_when_fingerprint_raises(
        self, tmp_path, monkeypatch
    ):
        from app.modules.media.mesh_protocol import ThumbnailOutput

        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        outputs = []

        def broken(prepared):
            assert len(outputs) == 2
            assert isinstance(outputs[1], ThumbnailOutput)
            assert outputs[1].image is not None
            raise ValueError("analysis failed after preview")

        monkeypatch.setattr(thumbnail_engine, "extract", broken)
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True), on_output=outputs.append
        )

        assert len(outputs) == 2
        assert outputs[1].image == result.image
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert (
            result.fingerprint_result.failure_code
            is FingerprintFailureCode.ANALYSIS_FAILED
        )
        assert result.volume == VolumeMeasured(1000.0)

    @pytest.mark.parametrize("error", [RuntimeError, MemoryError])
    def test_propagates_output_publication_failure(self, tmp_path, monkeypatch, error):
        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())
        fault = error("publication failed")
        outputs = []

        def fail(output):
            outputs.append(output)
            raise fault

        def render(*args, **kwargs):
            raise AssertionError("render must not start after publication failed")

        monkeypatch.setattr(mesh_render, "render_scene_thumbnail", render)
        with pytest.raises(error) as raised:
            ThumbnailEngine().generate(ThumbnailRequest(path), on_output=fail)

        assert raised.value is fault
        assert len(outputs) == 1
        assert outputs[0].volume == VolumeMeasured(1000.0)
