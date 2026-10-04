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
            pytest.param(2200, 1, id="one-face"),
            pytest.param(2199, 0, id="less-than-one-face"),
            pytest.param(4400, 2, id="two-faces"),
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
