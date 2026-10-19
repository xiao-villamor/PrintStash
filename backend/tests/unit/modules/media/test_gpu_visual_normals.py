"""Native normal canaries validate readback and retire all owned buffers."""

import sys
from unittest.mock import Mock

import numpy as np
import pytest

from app.modules.media.gpu_visual_normals import (
    VisualNormalPipeline,
    visual_scratch_sizes,
)
from app.runtime.compute.budget import Residency
from tests.fakes.gpu_device import API, Device


class TestVisualCanary:
    @pytest.mark.parametrize("outcome", ["healthy", "wrong", "status", "submit"])
    def test_validates_readback_before_releasing_memory(self, monkeypatch, outcome):
        monkeypatch.setitem(sys.modules, "wgpu", API)
        device = Device()
        memory = Residency(1024**2)
        fence = Mock()
        values = [
            np.tile([0.0, 0.0, 1.0], (6, 1)).astype("<f4").tobytes(),
            (1 if outcome == "status" else 0).to_bytes(4, "little"),
        ]
        if outcome == "wrong":
            values[0] = bytes(72)
        device.queue.read_buffer = Mock(side_effect=values)
        device.queue.fail_submit = outcome == "submit"
        pipeline = VisualNormalPipeline(device)
        if outcome == "healthy":
            assert pipeline.canary(memory=memory, fence=fence) is None
        else:
            with pytest.raises(RuntimeError if outcome == "submit" else ValueError):
                pipeline.canary(memory=memory, fence=fence)
        assert memory.used == 0
        assert all(buffer.destroyed for buffer in device.buffers)
        assert fence.wait.call_count == int(outcome == "submit")

    @pytest.mark.parametrize("vertices", [0, True, -1])
    def test_refuses_invalid_visual_scratch_shape(self, vertices):
        with pytest.raises(ValueError, match="compute_normals_shape"):
            visual_scratch_sizes(vertices)
