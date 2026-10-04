"""Legacy streaming cancellation follows the same worker-tree cleanup contract."""

import json
from pathlib import Path

import pytest

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media import stl_streaming
from app.modules.media.worker_bootstrap import command
from tests.factories import content


class TestStreamingCancellation:
    def test_cancellation_reaps_owned_workers(self, tmp_path, monkeypatch):
        source = tmp_path / "part.stl"
        source.write_bytes(content.binary_stl())
        pids = tmp_path / "pids"
        original = stl_streaming.subprocess.Popen
        processes = []

        def waiting(_argv, **kwargs):
            process = original(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree_wait", str(pids)],
                    256 * 1024**2,
                ),
                **kwargs,
            )
            processes.append(process)
            return process

        monkeypatch.setattr(stl_streaming.subprocess, "Popen", waiting)
        with cancellation_scope(pids.exists), pytest.raises(OperationCancelled):
            stl_streaming.render_stl_preview_isolated(source)
        assert processes[0].poll() is not None
        for pid in json.loads(pids.read_text()):
            assert not Path(f"/proc/{pid}").exists()
        assert set(tmp_path.iterdir()) == {source, pids}


class TestPreviewCoverage:
    def test_represents_remote_small_component(self, tmp_path: Path) -> None:
        import io

        import numpy as np
        import trimesh
        from PIL import Image

        sphere = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        cube = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
        cube.apply_translation((100.0, 0.0, 0.0))
        source = tmp_path / "disconnected.stl"
        source.write_bytes(
            trimesh.util.concatenate([sphere, cube]).export(file_type="stl")
        )

        result = stl_streaming.render_stl_preview_isolated(
            source, width=512, height=512
        )

        assert result is not None
        assert result.triangle_count == 5132
        assert result.bounds_max[0] == pytest.approx(101.0)
        with Image.open(io.BytesIO(result.png)) as image:
            alpha = np.asarray(image.getchannel("A"))
        assert np.any(alpha[:, :128])
        assert np.any(alpha[:, -128:])
        assert not np.any(alpha[:, 192:320])

    def test_preserves_long_appendage(self, tmp_path: Path) -> None:
        import io

        import numpy as np
        import trimesh
        from PIL import Image

        body = trimesh.creation.icosphere(subdivisions=4, radius=5.0)
        appendage = trimesh.creation.box(extents=(100.0, 2.0, 2.0))
        appendage.apply_translation((50.0, 0.0, 0.0))
        source = tmp_path / "long-appendage.stl"
        source.write_bytes(
            trimesh.util.concatenate([body, appendage]).export(file_type="stl")
        )

        result = stl_streaming.render_stl_preview_isolated(
            source, width=512, height=512
        )

        assert result is not None
        assert result.triangle_count == 5132
        assert result.bounds_min == pytest.approx((-5.0, -5.0, -5.0))
        assert result.bounds_max == pytest.approx((100.0, 5.0, 5.0))
        with Image.open(io.BytesIO(result.png)) as image:
            alpha = np.asarray(image.getchannel("A"))
        assert np.any(alpha[:, :128])
        assert np.any(alpha[:, 192:320])
        assert np.any(alpha[:, -128:])

    def test_preserves_ascii_render_after_translation(self, tmp_path: Path) -> None:
        import trimesh

        cube = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
        origin = tmp_path / "origin.stl"
        origin.write_text(trimesh.exchange.stl.export_stl_ascii(cube), encoding="ascii")
        cube.apply_translation((1e9, 1e9, 1e9))
        translated = tmp_path / "translated.stl"
        translated.write_text(
            trimesh.exchange.stl.export_stl_ascii(cube), encoding="ascii"
        )

        first = stl_streaming.render_stl_preview_isolated(origin, width=128, height=128)
        second = stl_streaming.render_stl_preview_isolated(
            translated, width=128, height=128
        )

        assert first is not None
        assert second is not None
        assert second.bounds_max[0] - second.bounds_min[0] == 10.0
        assert second.png == first.png

    def test_renders_finite_ascii_near_float32_max(self, tmp_path: Path) -> None:
        import io

        import numpy as np
        import trimesh
        from PIL import Image

        cube = trimesh.creation.box(extents=(6e38, 6e38, 6e38))
        source = tmp_path / "extreme.stl"
        source.write_text(trimesh.exchange.stl.export_stl_ascii(cube), encoding="ascii")

        result = stl_streaming.render_stl_preview_isolated(
            source, width=128, height=128
        )

        assert result is not None
        assert result.triangle_count == 12
        assert result.raster_candidates > 0
        assert result.bounds_min == (-3e38, -3e38, -3e38)
        assert result.bounds_max == (3e38, 3e38, 3e38)
        with Image.open(io.BytesIO(result.png)) as image:
            assert np.any(np.asarray(image.getchannel("A")))

    @pytest.mark.parametrize("encoding", ["binary", "ascii"], ids=str)
    def test_keeps_output_independent_of_chunk(
        self, tmp_path: Path, encoding: str
    ) -> None:
        source = tmp_path / "chunked.stl"
        source.write_bytes(
            content.binary_stl(triangles=120)
            if encoding == "binary"
            else content.ascii_stl(triangles=120)
        )

        small = stl_streaming.render_stl_preview_isolated(
            source,
            width=128,
            height=128,
            limits=stl_streaming.STLStreamingLimits(chunk_triangles=1),
        )
        large = stl_streaming.render_stl_preview_isolated(
            source,
            width=128,
            height=128,
            limits=stl_streaming.STLStreamingLimits(chunk_triangles=8192),
        )

        assert small is not None
        assert large is not None
        assert small.png == large.png
        assert small.bounds_min == large.bounds_min
        assert small.bounds_max == large.bounds_max


class TestValidTriangle:
    def test_renders_valid_oblique_facet(self, tmp_path: Path) -> None:
        import io

        from PIL import Image

        source = tmp_path / "oblique.stl"
        source.write_text(
            """solid oblique
facet normal -2 -2 4
outer loop
vertex 0 0 0
vertex 2 0 1
vertex 0 2 1
endloop
endfacet
endsolid oblique
""",
            encoding="ascii",
        )

        result = stl_streaming.render_stl_preview_isolated(
            source, width=128, height=128
        )

        assert result is not None
        assert result.parsed_triangles == 1
        assert result.raster_candidates > 0
        with Image.open(io.BytesIO(result.png)) as image:
            assert image.getchannel("A").getbbox() is not None


class TestReusableMeasurements:
    def test_refuses_scan_hints_outside_an_owned_worker(self, tmp_path, monkeypatch):
        from app.modules.media.stl_reader import scan_stl
        from app.modules.media.worker_bootstrap import WORKER_MARKER

        source = tmp_path / "part.stl"
        source.write_bytes(content.binary_stl())
        measurements = scan_stl(source)
        monkeypatch.delenv(WORKER_MARKER, raising=False)

        with pytest.raises(ValueError, match="measurements require an owned worker"):
            stl_streaming.render_stl_preview_isolated(source, measurements=measurements)
