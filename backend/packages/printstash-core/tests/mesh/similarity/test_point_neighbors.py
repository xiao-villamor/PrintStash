"""Point lookup preserves exact nearest-distance semantics across reuse and budgets."""

import time

import numpy as np
import pytest

from printstash_core.mesh.similarity.fingerprint import GeometryError
from printstash_core.mesh.similarity.point_neighbors import PointNeighbors


class TestPointNeighbors:
    @pytest.mark.parametrize("count", [128, 5000], ids=["ranking", "evaluation"])
    def test_matches_exhaustive_distances(self, count):
        rng = np.random.default_rng(154)
        target = rng.normal(size=(count, 3)) * [1, 0.01, 5]
        source = rng.normal(size=(200, 3))
        exact = np.hypot.reduce(source[:, None, :] - target[None, :, :], axis=2)
        expected = exact.argmin(axis=1)

        distances, indices = PointNeighbors(target).query(source)

        np.testing.assert_array_equal(indices, expected)
        np.testing.assert_allclose(
            distances, exact[np.arange(200), expected], rtol=1e-14
        )

    def test_resolves_ties_by_original_order(self):
        target = np.array(
            [[1.0, 0, 0], [-1.0, 0, 0], [1.0, 0, 0], [0, 1.0, 0], [0, -1.0, 0]]
        )

        distances, indices = PointNeighbors(target).query(np.zeros((600, 3)))

        np.testing.assert_array_equal(indices, np.zeros(600, dtype=int))
        np.testing.assert_array_equal(distances, np.ones(600))

    def test_owns_target_snapshot(self):
        target = np.array([[0.0, 0, 0], [1.0, 0, 0]])
        index = PointNeighbors(target)
        target[:] = 100

        distances, indices = index.query(np.array([[0.25, 0, 0]]))

        np.testing.assert_array_equal(distances, [0.25])
        np.testing.assert_array_equal(indices, [0])

    def test_reuses_index_for_independent_queries(self):
        index = PointNeighbors(np.array([[0.0, 0, 0], [2.0, 0, 0]]))
        first = index.query(np.array([[0.25, 0, 0]]))

        second = index.query(np.array([[1.5, 0, 0]]))

        np.testing.assert_array_equal(first[0], [0.25])
        np.testing.assert_array_equal(first[1], [0])
        np.testing.assert_array_equal(second[0], [0.5])
        np.testing.assert_array_equal(second[1], [1])

    def test_accepts_empty_source(self):
        index = PointNeighbors(np.zeros((1, 3)))

        distances, indices = index.query(np.empty((0, 3)))

        assert distances.shape == indices.shape == (0,)

    def test_rejects_empty_target(self):
        with pytest.raises(GeometryError, match="empty_target"):
            PointNeighbors(np.empty((0, 3)))

    @pytest.mark.parametrize(
        "points",
        [
            np.zeros((2, 2)),
            np.zeros(3),
            np.array([[np.nan, 0, 0]]),
            np.array([[np.inf, 0, 0]]),
            np.array([["x", "y", "z"]]),
            np.array([[2**53 + 1, 0, 0]], dtype=np.int64),
        ],
        ids=["dimensions", "rank", "nan", "infinity", "text", "integers"],
    )
    def test_rejects_malformed_target(self, points):
        with pytest.raises(GeometryError, match="invalid_point_cloud"):
            PointNeighbors(points)

    @pytest.mark.parametrize(
        "points",
        [np.zeros((2, 2)), np.array([[np.nan, 0, 0]])],
        ids=["dimensions", "nan"],
    )
    def test_rejects_malformed_source(self, points):
        index = PointNeighbors(np.zeros((1, 3)))

        with pytest.raises(GeometryError, match="invalid_point_cloud"):
            index.query(points)

    def test_rejects_target_over_sample_cap(self):
        with pytest.raises(GeometryError, match="point_limit"):
            PointNeighbors(np.zeros((10001, 3)))

    def test_rejects_source_over_sample_cap(self):
        index = PointNeighbors(np.zeros((1, 3)))

        with pytest.raises(GeometryError, match="point_limit"):
            index.query(np.zeros((10001, 3)))

    def test_accepts_sample_cap(self):
        index = PointNeighbors(np.zeros((10000, 3)))

        distances, indices = index.query(np.ones((10000, 3)))

        np.testing.assert_array_equal(indices, np.zeros(10000, dtype=int))
        np.testing.assert_allclose(distances, np.sqrt(3))

    def test_rejects_expired_build(self):
        with pytest.raises(GeometryError, match="verification_time_limit"):
            PointNeighbors(np.zeros((1, 3)), deadline=time.monotonic() - 1)

    def test_rejects_expired_query(self):
        index = PointNeighbors(np.zeros((1, 3)))

        with pytest.raises(GeometryError, match="verification_time_limit"):
            index.query(np.zeros((1, 3)), deadline=time.monotonic() - 1)

    def test_checks_deadline_between_blocks(self, monkeypatch):
        from printstash_core.mesh.similarity import time_budget

        index = PointNeighbors(np.array([[0.0, 0, 0], [2.0, 0, 0]]))
        ticks = iter([0.0, 0.0, 0.0, 2.0, 2.0])
        monkeypatch.setattr(time_budget.time, "monotonic", lambda: next(ticks))

        with pytest.raises(GeometryError, match="verification_time_limit"):
            index.query(np.full((1000, 3), 0.25), deadline=1.0)

    def test_checks_deadline_during_tie_resolution(self, monkeypatch):
        from printstash_core.mesh.similarity import time_budget

        index = PointNeighbors(np.array([[0.0, 0, 0], [2.0, 0, 0]]))
        ticks = iter([0.0, 0.0, 0.0, 2.0, 2.0])
        monkeypatch.setattr(time_budget.time, "monotonic", lambda: next(ticks))

        with pytest.raises(GeometryError, match="verification_time_limit"):
            index.query(np.ones((100, 3)), deadline=1.0)

    @pytest.mark.parametrize("scale", [1e-200, 1e200], ids=["tiny", "large"])
    def test_preserves_finite_scale(self, scale):
        target = np.array([[0.0, 0, 0], [4.0, 0, 0]]) * scale

        distances, indices = PointNeighbors(target).query(
            np.array([[3.0, 0, 0]]) * scale
        )

        np.testing.assert_array_equal(indices, [1])
        assert distances[0] / scale == pytest.approx(1.0)

    def test_rejects_index_precision_loss(self):
        target = np.array([[1e300, 0, 0], [1e-300, 0, 0]])

        with pytest.raises(GeometryError, match="numeric_range"):
            PointNeighbors(target)

    def test_rejects_unrepresentable_distance(self):
        index = PointNeighbors(np.array([[1e308, 0, 0]]))

        with pytest.raises(GeometryError, match="numeric_range"):
            index.query(np.array([[-1e308, 0, 0]]))

    @pytest.mark.parametrize(
        "failure_type",
        [ValueError, FloatingPointError],
        ids=["invalid-native-index", "native-floating-point-error"],
    )
    def test_reports_native_index_build_failure(self, monkeypatch, failure_type):
        import scipy.spatial

        failure = failure_type("native index cannot represent the target")

        def reject_index(*args, **kwargs):
            raise failure

        monkeypatch.setattr(scipy.spatial, "KDTree", reject_index)

        with pytest.raises(GeometryError, match="^numeric_range$") as caught:
            PointNeighbors(np.zeros((1, 3)))

        assert caught.value.__cause__ is failure

    def test_rejects_native_distance_overflow(self):
        index = PointNeighbors(np.zeros((1, 3)))
        source = np.array([[1e200, 0.0, 0.0]])

        with pytest.raises(GeometryError, match="^numeric_range$"):
            index.query(source)

    def test_rejects_unrepresentable_physical_norm(self):
        target = np.array([[1.3e308, 1.3e308, 0.0]])
        index = PointNeighbors(target)

        with pytest.raises(GeometryError, match="^numeric_range$"):
            index.query(np.zeros((1, 3)))
