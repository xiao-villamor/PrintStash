"""Evaluation cannot waive geometry validation or bounded resource admission."""

import numpy as np
import pytest
from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
from printstash_core.mesh.render_geometry import PreparedRender

from app.modules.media.compute_geometry import encode
from app.modules.media.compute_render import RECIPE
from app.runtime.compute.render_policy import admission, admission_many


@pytest.fixture
def frame():
    prepared = PreparedRender(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
        1,
        lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )
    return encode(
        prepared, 640, 480, [None], False
    ), f"{RECIPE}:{RASTERIZER_RECIPE}:640x480:1:0"


class TestAdmission:
    def test_accounts_for_supersampled_buffers(self, frame):
        payload, recipe = frame
        result = admission(payload, recipe, 1)
        assert result.device_bytes >= 1280 * 960 * 12
        assert result.host_bytes > 3 * len(payload) + 1280 * 960 * 96

    @pytest.mark.parametrize(
        "recipe,units", [("stale", 1), (None, 2)], ids=["recipe", "units"]
    )
    def test_rejects_mislabelled_work(self, frame, recipe, units):
        payload, current = frame
        with pytest.raises(ValueError, match="compute_render_identity"):
            admission(payload, current if recipe is None else recipe, units)

    def test_rejects_malformed_geometry(self):
        with pytest.raises(ValueError, match="compute_geometry"):
            admission(b"bad", "recipe", 1)

    def test_batches_share_workspace(self, frame):
        payload, recipe = frame
        single = admission(payload, recipe, 1)
        batch = admission_many([payload] * 8, [recipe] * 8, [1] * 8)
        assert single.device_bytes < batch.device_bytes < 8 * single.device_bytes
        assert single.host_bytes < batch.host_bytes < 8 * single.host_bytes

    def test_refuses_more_than_eight_frames(self, frame):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        payload, recipe = frame
        with pytest.raises(ComputeUnavailable) as exc:
            admission_many([payload] * 9, [recipe] * 9, [1] * 9)
        assert exc.value.reason is Reason.CAPACITY

    def test_accounts_for_binary_owner_workspace(self, frame):
        from app.modules.media.compute_geometry import decode
        from app.modules.media.gpu_postprocess import workspace_bytes
        from app.runtime.compute.render_policy import admission_decoded

        payload, recipe = frame
        legacy = admission(payload, recipe, 1)
        binary = admission_decoded(
            [decode(payload)], [len(payload)], [recipe], [1], gpu_finalize=True
        )
        assert binary.device_bytes == legacy.device_bytes + workspace_bytes(640, 480, 2)
        assert binary.host_bytes >= binary.readback_bytes + 96 * 1024**2
        assert binary.host_bytes < legacy.host_bytes
