"""Independent visual-policy boundaries; no GPU context or device proof."""

import hashlib
import io
import json
from dataclasses import replace

import pytest
from PIL import Image
from printstash_core.mesh.rasterizer import RenderedPixels

from scripts.gpu_render_measurement import (
    Flow,
    Mode,
    OutputFormat,
    PilotSpec,
    compare_pixels,
    measure,
)
from tests.paths import FIXTURES_DIR


@pytest.fixture
def pilot_spec(tmp_path):
    return PilotSpec(
        source=str(tmp_path / "source.3mf"),
        output=str(tmp_path / "output"),
        case=None,
        mode=Mode.CPU,
        trials=1,
        backend="egl",
        chunk_size=5,
        allocation_limit=512 * 1024**2,
        width=640,
        height=480,
        views=1,
    )


class TestPilotSpec:
    @pytest.mark.parametrize(
        ("trials", "views", "embedding_size"),
        [pytest.param(1, 1, 32, id="minimum"), pytest.param(100, 6, 512, id="maximum")],
    )
    def test_accepts_bounded_analytic_requests(
        self, pilot_spec, trials, views, embedding_size
    ):
        actual = replace(
            pilot_spec,
            flow=Flow.ANALYTIC,
            trials=trials,
            views=views,
            embedding_size=embedding_size,
            width=1,
            height=1,
            chunk_size=1,
            allocation_limit=1,
        )

        assert (actual.trials, actual.views, actual.embedding_size) == (
            trials,
            views,
            embedding_size,
        )
        assert (
            actual.width,
            actual.height,
            actual.chunk_size,
            actual.allocation_limit,
        ) == (1, 1, 1, 1)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"mode": "cpu"}, id="untyped-mode"),
            pytest.param({"backend": "osmesa"}, id="unsupported-backend"),
            pytest.param({"flow": "preview"}, id="untyped-flow"),
            pytest.param({"output_format": "PNG"}, id="untyped-format"),
        ],
    )
    def test_refuses_invalid_execution_modes(self, pilot_spec, changes):
        with pytest.raises(ValueError, match="^invalid_gpu_pilot_mode$"):
            replace(pilot_spec, **changes)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"trials": 0}, id="zero-trials"),
            pytest.param({"chunk_size": 0}, id="zero-chunk"),
            pytest.param({"allocation_limit": 0}, id="zero-allowance"),
            pytest.param({"width": 0}, id="zero-width"),
            pytest.param({"height": -1}, id="negative-height"),
            pytest.param({"views": 0}, id="zero-views"),
            pytest.param({"embedding_size": 0}, id="zero-embedding"),
            pytest.param({"trials": True}, id="boolean-trials"),
            pytest.param({"width": 1.5}, id="fractional-width"),
        ],
    )
    def test_refuses_invalid_work_dimensions(self, pilot_spec, changes):
        with pytest.raises(ValueError, match="^invalid_gpu_pilot_dimensions$"):
            replace(pilot_spec, **changes)

    @pytest.mark.parametrize(
        "size",
        [pytest.param(31, id="below-minimum"), pytest.param(513, id="above-maximum")],
    )
    def test_refuses_invalid_embedding_sizes(self, pilot_spec, size):
        with pytest.raises(ValueError, match="^invalid_embedding_size$"):
            replace(pilot_spec, embedding_size=size)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"trials": 101}, id="trials"),
            pytest.param({"views": 7}, id="views"),
        ],
    )
    def test_refuses_excessive_trial_counts(self, pilot_spec, changes):
        with pytest.raises(ValueError, match="^invalid_gpu_pilot_trial_count$"):
            replace(pilot_spec, flow=Flow.ANALYTIC, **changes)

    @pytest.mark.parametrize(
        "flow", [Flow.PREVIEW, Flow.MULTIVIEW], ids=lambda flow: flow.value
    )
    def test_confines_extra_views_to_analytic_flow(self, pilot_spec, flow):
        with pytest.raises(ValueError, match="^views_require_analytic_flow$"):
            replace(pilot_spec, flow=flow, views=2)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"width": 320}, id="width"),
            pytest.param({"height": 240}, id="height"),
            pytest.param({"output_format": OutputFormat.PNG}, id="codec"),
        ],
    )
    def test_refuses_noncanonical_multiview_requests(self, pilot_spec, changes):
        with pytest.raises(
            ValueError, match="^multiview_requires_canonical_thumbnail$"
        ):
            replace(pilot_spec, flow=Flow.MULTIVIEW, **changes)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"source": "source.3mf"}, id="source"),
            pytest.param({"output": "output"}, id="output"),
        ],
    )
    def test_refuses_relative_artifact_paths(self, pilot_spec, changes):
        with pytest.raises(ValueError, match="^gpu_pilot_requires_absolute_paths$"):
            replace(pilot_spec, **changes)

    def test_refuses_unknown_source_controls(self, pilot_spec):
        with pytest.raises(ValueError, match="^unknown_gpu_pilot_control$"):
            replace(pilot_spec, case="not-a-corpus-control")


class TestComparePixels:
    def test_refuses_a_changed_foreground_mask(self):
        reference = RenderedPixels(2, 1, bytes([100, 100, 100, 128, 50, 50, 50, 255]))
        candidate = RenderedPixels(2, 1, bytes([100, 100, 100, 127, 50, 50, 50, 255]))

        result = compare_pixels(reference, candidate)

        assert result["both_have_foreground"] is True
        assert result["foreground_mask_differing_pixels"] == 1
        assert result["rgba_max_difference"] == 1
        assert result["accepted"] is False

    def test_accepts_colour_error_at_the_boundary(self):
        reference = RenderedPixels(2, 1, bytes([100, 100, 100, 255, 0, 0, 0, 0]))
        candidate = RenderedPixels(2, 1, bytes([108, 108, 108, 255, 0, 0, 0, 0]))

        result = compare_pixels(reference, candidate)

        assert result["both_have_foreground"] is True
        assert result["foreground_mask_differing_pixels"] == 0
        assert result["rgba_max_difference"] == 8
        assert result["accepted"] is True
        assert (
            result["reference_rgba_sha256"]
            == hashlib.sha256(reference.rgba).hexdigest()
        )
        assert (
            result["candidate_rgba_sha256"]
            == hashlib.sha256(candidate.rgba).hexdigest()
        )

    def test_refuses_colour_error_above_the_boundary(self):
        reference = RenderedPixels(2, 1, bytes([100, 100, 100, 255, 0, 0, 0, 0]))
        candidate = RenderedPixels(2, 1, bytes([109, 109, 109, 255, 0, 0, 0, 0]))

        result = compare_pixels(reference, candidate)

        assert result["both_have_foreground"] is True
        assert result["foreground_mask_differing_pixels"] == 0
        assert result["rgba_max_difference"] == 9
        assert result["accepted"] is False

    def test_refuses_equal_empty_frames(self):
        reference = RenderedPixels(2, 1, bytes(8))
        candidate = RenderedPixels(2, 1, bytes(8))

        result = compare_pixels(reference, candidate)

        assert result["both_have_foreground"] is False
        assert result["foreground_mask_differing_pixels"] == 0
        assert result["rgba_max_difference"] == 0
        assert result["accepted"] is False

    def test_refuses_mismatched_dimensions(self):
        rgba = bytes([100, 100, 100, 255, 0, 0, 0, 0])
        reference = RenderedPixels(2, 1, rgba)
        candidate = RenderedPixels(1, 2, rgba)

        with pytest.raises(ValueError, match="^pixel_dimensions_mismatch$"):
            compare_pixels(reference, candidate)


class TestRenderDiagnostics:
    def test_preserves_caught_renderer_exception(self):
        from scripts.gpu_render_measurement import RenderDiagnostics, exception_details

        diagnostics = RenderDiagnostics()
        original = MemoryError("bounded host allocation failed")

        try:
            raise original
        except MemoryError:
            diagnostics.warning("render failed", exc_info=True)

        assert diagnostics.failure is original
        assert exception_details(diagnostics.failure)["diagnostic"] == (
            "MemoryError: bounded host allocation failed"
        )

    def test_keeps_silhouette_warning_nonfatal(self):
        from scripts.gpu_render_measurement import RenderDiagnostics

        diagnostics = RenderDiagnostics()

        diagnostics.warning("using silhouette", exc_info=False)

        assert diagnostics.failure is None


class TestMeasure:
    @pytest.mark.parametrize(
        "output_format", list(OutputFormat), ids=lambda value: value.value
    )
    def test_preserves_cpu_preview_output(self, tmp_path, pilot_spec, output_format):
        from app.modules.media import mesh_render
        from app.modules.media.three_mf_scene import read_scene
        from scripts.viewer_representation_corpus import write_sources

        source = write_sources(tmp_path / "sources", ("reflection",))["reflection"]
        original = source.read_bytes()
        scene = read_scene(source)
        expected = mesh_render.render_scene_thumbnail(
            scene, source.name, width=64, height=48, output_format=output_format.value
        )
        oracle_png = mesh_render.render_scene_thumbnail(
            scene, source.name, width=64, height=48, output_format="PNG"
        )
        assert expected is not None
        assert oracle_png is not None
        with Image.open(io.BytesIO(oracle_png)) as image:
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            expected_rgb = white.convert("RGB").tobytes()
        output = tmp_path / "measurement"
        spec = replace(
            pilot_spec,
            source=str(source),
            output=str(output),
            width=64,
            height=48,
            output_format=output_format,
        )

        report = measure(spec)

        assert report["mode"] == Mode.CPU
        assert report["flow"] == Flow.PREVIEW
        assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
        assert report["source_unchanged"] is True
        assert source.read_bytes() == original
        assert len(report["observations"]) == 1
        observation = report["observations"][0]
        assert observation["method"] == "cpu"
        assert observation["status"] == "completed", observation
        assert len(observation["frames"]) == 1
        frame = observation["frames"][0]
        assert frame["encoded_sha256"] == hashlib.sha256(expected).hexdigest()
        assert frame["encoded_bytes"] == len(expected)
        assert frame["rgb_sha256"] == hashlib.sha256(expected_rgb).hexdigest()
        assert (frame["rgb_width"], frame["rgb_height"]) == (64, 48)
        assert frame["matte"] is False
        assert (
            output / f"cpu-view0.{output_format.value.lower()}"
        ).read_bytes() == expected
        assert report["quality"] == []
        assert report["gpu_fixed_backend_chunk_determinism_failures"] == []

    def test_preserves_frozen_cpu_multiview_inputs(self, tmp_path, pilot_spec):
        from tests.factories.geometry import tetrahedron

        baseline = json.loads(
            (FIXTURES_DIR / "media/visual-prepared-v1.json").read_text()
        )
        source = tmp_path / "tetra.stl"
        original = tetrahedron().export(file_type="stl")
        source.write_bytes(original)
        assert hashlib.sha256(original).hexdigest() == baseline["source_sha256"]
        spec = replace(
            pilot_spec,
            source=str(source),
            flow=Flow.MULTIVIEW,
            embedding_size=baseline["image_size"],
        )

        report = measure(spec)

        assert report["source_unchanged"] is True
        assert source.read_bytes() == original
        assert report["source_sha256"] == baseline["source_sha256"]
        assert report["flow"] == Flow.MULTIVIEW
        assert len(report["observations"]) == 1
        observation = report["observations"][0]
        assert observation["status"] == "completed", observation
        assert observation["method"] == "cpu"
        frames = observation["frames"]
        assert len(frames) == 7
        assert [frame["rgb_sha256"] for frame in frames] == [
            baseline["thumbnail_rgb_sha256"],
            *baseline["views_rgb_sha256"],
        ]
        assert [(frame["width"], frame["height"]) for frame in frames] == [
            (640, 480)
        ] + [(32, 32)] * 6
        assert [(frame["rgb_width"], frame["rgb_height"]) for frame in frames] == [
            (32, 32)
        ] * 7
        assert [frame["matte"] for frame in frames] == [False] + [True] * 6
        assert [frame["encoded_bytes"] for frame in frames[1:]] == [None] * 6
        assert frames[0]["encoded_bytes"] > 0
        assert report["quality"] == []
