"""View workspace and bounded reduction contracts, independent of hardware."""

import sys

import numpy as np
import pytest

from app.modules.media.gpu_render_layout import (
    VIEW_WORKSPACE_BYTES,
    bounds_scratch_sizes,
)
from app.modules.media.gpu_views import ViewPipeline, frame_config
from tests.fakes.gpu_device import API, Device


class TestViewPreparation:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [(3, ()), (64, ()), (65, (64,)), (4096, (2048,)), (4097, (2080, 64))],
    )
    def test_bounds_reduction_charges_every_intermediate_level(self, count, expected):
        assert bounds_scratch_sizes(count) == expected

    def test_aligns_camera_configuration_slots(self):
        automatic = frame_config(None, 400, 300, False)
        explicit = frame_config(np.eye(3), 400, 300, True)
        assert len(automatic) == len(explicit) == 256
        auto = np.frombuffer(automatic, dtype="<f4").reshape(16, 4)
        fixed = np.frombuffer(explicit, dtype="<f4").reshape(16, 4)
        np.testing.assert_array_equal(fixed[:3, :3], np.eye(3))
        assert auto[3, 3] == 1 and fixed[3, 3] == 0
        assert auto[5, 0] > 0 and fixed[5, 0] == 0
        assert VIEW_WORKSPACE_BYTES == 8 * 256 * 2

    def test_large_reduction_uses_bounded_two_dimensional_dispatch(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "wgpu", API)
        device = Device()
        device.limits["max-compute-workgroups-per-dimension"] = 4
        pipeline = ViewPipeline(device)
        positions = device.create_buffer(size=640 * 12, usage=128)
        scratch = [
            device.create_buffer(size=size, usage=128)
            for size in bounds_scratch_sizes(640)
        ]
        target = device.create_buffer(size=32, usage=128)
        pipeline.encode_bounds(
            device.create_command_encoder(), positions, 640, target, scratch
        )
        assert device.dispatches == [(4, 3, 1), (1, 1, 1)]
