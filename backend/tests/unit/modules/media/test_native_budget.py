"""Weighted admission preserves startup capacity without dividing large-job RAM."""

import pytest

from app.modules.media import native_budget as budget
from app.runtime.native_admission import Resources


class TestCapacity:
    def test_preserves_whole_memory_across_slots(self):
        assert budget.capacity(8 * 1024**3, 0.5, 4) == Resources(4, 4 * 1024**3)

    def test_disabled_estimates_retain_containment(self):
        assert budget.capacity(8 * 1024**3, 0, 2).bytes == 4 * 1024**3

    def test_undetected_memory_uses_bounded_fallback(self):
        assert budget.capacity(None, 0.5, 2).bytes == budget.FALLBACK_MEMORY

    def test_zero_slots_remains_serial(self):
        assert budget.capacity(None, 0.5, 0).slots == 1

    @pytest.mark.parametrize(
        "memory,fraction,slots",
        [
            pytest.param(-1, 0.5, 1, id="negative-memory"),
            pytest.param(True, 0.5, 1, id="boolean-memory"),
            pytest.param(100, float("nan"), 1, id="nonfinite-fraction"),
            pytest.param(100, 1.1, 1, id="excess-fraction"),
            pytest.param(100, True, 1, id="boolean-fraction"),
            pytest.param(100, 0.5, -1, id="negative-slots"),
            pytest.param(100, 0.5, True, id="boolean-slots"),
        ],
    )
    def test_refuses_invalid_configuration(self, memory, fraction, slots):
        with pytest.raises(ValueError):
            budget.capacity(memory, fraction, slots)


class TestRequest:
    def test_small_source_retains_startup_headroom(self):
        assert budget.request(Resources(4, 4 * 1024**3), (2,)) == Resources(
            1, 512 * 1024**2
        )

    def test_weights_known_sources_together(self):
        amount = budget.request(Resources(4, 4 * 1024**3), (300_000, 200_000))

        assert amount == Resources(1, 500_000 * budget.DEFAULT_FACE_BYTES)

    def test_unknown_source_requires_whole_memory(self):
        assert (
            budget.request(Resources(4, 4 * 1024**3), (1000, None)).bytes == 4 * 1024**3
        )

    def test_oversized_source_retains_hard_ceiling(self):
        assert budget.request(Resources(4, 1024**3), (10_000_000,)).bytes == 1024**3

    @pytest.mark.parametrize(
        "counts",
        [
            pytest.param((), id="empty"),
            pytest.param((-1,), id="negative"),
            pytest.param((True,), id="boolean"),
            pytest.param((1.5,), id="noninteger"),
        ],
    )
    def test_refuses_invalid_counts(self, counts):
        with pytest.raises(ValueError):
            budget.request(Resources(1, 1024**3), counts)


class TestFaceCapacity:
    @pytest.mark.parametrize(
        "memory,expected",
        [
            pytest.param(1, 0, id="below-one-face"),
            pytest.param(3000, 1, id="one-face"),
            pytest.param(2999, 0, id="less-than-one-face"),
            pytest.param(6000, 2, id="two-faces"),
        ],
    )
    def test_matches_the_whole_pipeline_cost(self, memory, expected):
        assert budget.face_capacity(memory, ".stl") == expected

    def test_uses_format_specific_cost(self):
        assert budget.face_capacity(3600, ".3mf") == 1

    @pytest.mark.parametrize(
        "memory", [0, -1, True, 1.5], ids=["zero", "negative", "boolean", "noninteger"]
    )
    def test_refuses_invalid_memory(self, memory):
        with pytest.raises(ValueError):
            budget.face_capacity(memory, ".stl")


class TestWorkProfiles:
    @pytest.mark.parametrize(
        "kind,expected",
        [
            pytest.param("geometry", 512 * 1024**2, id="geometry"),
            pytest.param("png", 512 * 1024**2, id="png"),
            pytest.param("rgb", 512 * 1024**2, id="rgb"),
            pytest.param("webp", 640 * 1024**2, id="webp"),
            pytest.param("analysis", 1024**3, id="analysis"),
            pytest.param("analysis-webp", 1024**3, id="analysis-webp"),
        ],
    )
    def test_reserves_the_measured_workload_allowance(self, kind, expected):
        work = self.work(kind)
        assert budget.request(Resources(4, 4 * 1024**3), (12,), work=work) == Resources(
            1, expected
        )

    @staticmethod
    def work(kind):
        if kind == "geometry":
            return budget.GeometryWork()
        if kind == "analysis":
            return budget.AnalysisWork()
        if kind == "analysis-webp":
            return budget.AnalysisWork(
                budget.RasterWork(640, 480, 1, budget.RasterCodec.WEBP)
            )
        return budget.RasterWork(640, 480, 1, budget.RasterCodec(kind))

    def test_preserves_whole_pipeline_face_weight_without_double_baseline(self):
        amount = budget.request(
            Resources(4, 4 * 1024**3), (500_000,), work=budget.AnalysisWork()
        )
        assert amount == Resources(1, 1_500_000_000)

    @pytest.mark.parametrize(
        "kind", ["geometry", "png", "webp", "analysis", "analysis-webp"]
    )
    def test_unknown_complexity_keeps_the_full_pool_claim(self, kind):
        pool = Resources(4, 4 * 1024**3)
        assert budget.request(pool, (12, None), work=self.work(kind)) == Resources(
            1, pool.bytes
        )

    def test_caps_workload_allowance_at_the_hard_pool_ceiling(self):
        pool = Resources(4, 128 * 1024**2)
        assert budget.request(pool, (12,), work=budget.AnalysisWork()) == Resources(
            1, pool.bytes
        )

    def test_refuses_untyped_work(self):
        with pytest.raises(TypeError):
            budget.request(Resources(4, 4 * 1024**3), (12,), work=object())

    @pytest.mark.parametrize(
        "width,height,frames",
        [
            pytest.param(0, 480, 1, id="zero-width"),
            pytest.param(-1, 480, 1, id="negative-width"),
            pytest.param(True, 480, 1, id="boolean-width"),
            pytest.param(1.5, 480, 1, id="fractional-width"),
            pytest.param(640, 0, 1, id="zero-height"),
            pytest.param(640, True, 1, id="boolean-height"),
            pytest.param(640, 1.5, 1, id="fractional-height"),
            pytest.param(640, 480, 0, id="zero-frames"),
            pytest.param(640, 480, -1, id="negative-frames"),
            pytest.param(640, 480, True, id="boolean-frames"),
            pytest.param(640, 480, 1.5, id="fractional-frames"),
        ],
    )
    def test_refuses_invalid_raster_shape(self, width, height, frames):
        with pytest.raises(ValueError):
            budget.RasterWork(width, height, frames, budget.RasterCodec.PNG)

    @pytest.mark.parametrize("codec", ["PNG", "webp", object()])
    def test_requires_a_closed_raster_codec(self, codec):
        with pytest.raises(TypeError):
            budget.RasterWork(640, 480, 1, codec)

    def test_refuses_invalid_analysis_raster(self):
        with pytest.raises(TypeError):
            budget.AnalysisWork(object())

    @pytest.mark.parametrize(
        "width,height,frames,extra",
        [
            pytest.param(1280, 960, 1, 58_982_400, id="larger-frame"),
            pytest.param(640, 480, 6, 98_304_000, id="retained-frames"),
        ],
    )
    def test_adds_only_incremental_pixels_to_the_qualified_source_peak(
        self, width, height, frames, extra
    ):
        work = budget.RasterWork(width, height, frames, budget.RasterCodec.PNG)
        amount = budget.request(Resources(4, 4 * 1024**3), (500_000,), work=work)
        assert amount == Resources(1, 1_500_000_000 + extra)

    def test_large_raster_keeps_the_hard_pool_ceiling(self):
        pool = Resources(4, 2 * 1024**3)
        work = budget.RasterWork(16_384, 16_384, 6, budget.RasterCodec.RGB)
        assert budget.request(pool, (12,), work=work) == Resources(1, pool.bytes)

    @pytest.mark.parametrize(
        "faces,analysis,expected",
        [
            pytest.param(225_706, False, 902_824_000, id="qualified-benchy-webp"),
            pytest.param(500_000, False, 2_000_000_000, id="larger-webp"),
            pytest.param(225_706, True, 1024**3, id="analysis-webp-minimum"),
            pytest.param(500_000, True, 2_000_000_000, id="analysis-webp-face-cost"),
        ],
    )
    def test_webp_face_weight_covers_rendering_residency(
        self, faces, analysis, expected
    ):
        raster = budget.RasterWork(640, 480, 1, budget.RasterCodec.WEBP)
        work = budget.AnalysisWork(raster) if analysis else raster
        assert budget.request(
            Resources(4, 4 * 1024**3), (faces,), work=work
        ) == Resources(1, expected)

    @pytest.mark.parametrize("kind", ["geometry", "png", "rgb", "analysis"])
    def test_non_webp_work_retains_its_qualified_face_weight(self, kind):
        assert budget.request(
            Resources(4, 4 * 1024**3), (500_000,), work=self.work(kind)
        ) == Resources(1, 1_500_000_000)
