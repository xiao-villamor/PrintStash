"""The preview worker reports each expected failure as its closed reason."""

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.preview_contracts import PreviewLimitError
from app.modules.media.preview_worker import failure_reason


class TestFailureReason:
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (PreviewLimitError(), ThumbnailFailureReason.RESOURCE_LIMIT),
            (
                MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED),
                ThumbnailFailureReason.SOURCE_CHANGED,
            ),
            (
                GeometryError("scene_resource_limit"),
                ThumbnailFailureReason.RESOURCE_LIMIT,
            ),
            (
                GeometryError("step_unavailable"),
                ThumbnailFailureReason.UNSUPPORTED_CAPABILITY,
            ),
            (GeometryError("degenerate"), ThumbnailFailureReason.INVALID_SOURCE),
            (ImportError("no step"), ThumbnailFailureReason.UNSUPPORTED_CAPABILITY),
            (ValueError("degenerate"), ThumbnailFailureReason.INVALID_SOURCE),
        ],
    )
    def test_maps_expected_failures(self, error, reason):
        assert failure_reason(error) is reason

    def test_refuses_to_classify_unexpected_errors(self):
        with pytest.raises(TypeError, match="unexpected_preview_failure"):
            failure_reason(RuntimeError("bug"))
