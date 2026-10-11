"""Real-device visual-normal checks; selected explicitly in the GPU diagnostic lane."""

from __future__ import annotations

import time

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRenderGeometry

from app.modules.media.gpu_geometry import CompletionFence
from app.modules.media.gpu_normals import VisualRender, canonical
from app.modules.media.gpu_visual_normals import (
    VisualNormalPipeline,
    visual_scratch_sizes,
)
from app.runtime.compute.budget import Residency

from .webgpu_render_diagnostic import gpu_device as gpu_device


class TestVisualNormalPipeline:
    def test_executes_startup_canary_with_bounded_ownership(self, gpu_device):
        memory = Residency(1024**2)
        fence = CompletionFence(gpu_device, memory)
        try:
            VisualNormalPipeline(gpu_device).canary(memory=memory, fence=fence)
            assert memory.used == 4
        finally:
            fence.close()
        assert memory.used == 0

    @pytest.mark.parametrize(
        "case", ["duplicate", "high_degree", "opposed", "degenerate"], ids=str
    )
    def test_limits_unhealthy_neighborhoods_to_local_flat_normals(
        self, gpu_device, case
    ):
        import wgpu

        vertices = np.asarray(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [0, 1, 0], [0, 0, 1]],
            dtype="<f4",
        )
        faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype="<u4")
        if case == "high_degree":
            faces = np.tile(faces[:1], (4097, 1))
        elif case == "opposed":
            faces = np.asarray([[0, 1, 2], [0, 2, 1]], dtype="<u4")
        elif case == "degenerate":
            faces = np.asarray([[0, 1, 2], [3, 3, 3]], dtype="<u4")
        prepared = VisualRender(
            PreparedRenderGeometry(vertices, len(faces), lambda size: iter([faces]))
        )
        memory = Residency(1024**2)
        fence = CompletionFence(gpu_device, memory)
        owned = []
        total = (
            vertices.nbytes
            + faces.nbytes
            + len(vertices) * 16
            + sum(visual_scratch_sizes(len(vertices)))
        )
        memory.reserve(
            "normal-test",
            total,
            lambda: [buffer.destroy() for buffer in owned],
            time.monotonic(),
        )
        memory.pin("normal-test", time.monotonic())
        complete = False
        try:
            buffers = []
            for size in (
                vertices.nbytes,
                faces.nbytes,
                len(vertices) * 4,
                len(vertices) * 12,
            ):
                buffer = gpu_device.create_buffer(
                    size=size,
                    usage=wgpu.BufferUsage.STORAGE
                    | wgpu.BufferUsage.COPY_SRC
                    | wgpu.BufferUsage.COPY_DST,
                )
                buffers.append(buffer)
                owned.append(buffer)
            gpu_device.queue.write_buffer(buffers[0], 0, vertices)
            gpu_device.queue.write_buffer(buffers[1], 0, faces)
            encoder = gpu_device.create_command_encoder()
            result = VisualNormalPipeline(gpu_device).encode(
                prepared,
                encoder,
                positions=buffers[0],
                faces=buffers[1],
                identities=buffers[2],
                smoothed=buffers[3],
                owned=owned,
            )
            gpu_device.queue.submit([encoder.finish()])
            normals = np.frombuffer(
                gpu_device.queue.read_buffer(result.smooth), dtype="<f4"
            ).reshape(-1, 3)
            identities = np.frombuffer(
                gpu_device.queue.read_buffer(buffers[2]), dtype="<u4"
            )
            repairs = int.from_bytes(
                gpu_device.queue.read_buffer(result.status), "little"
            )
            complete = True
            assert np.isfinite(normals).all()
            np.testing.assert_array_equal(identities, np.arange(len(vertices)))
            if case == "duplicate":
                expected = canonical(prepared)
                np.testing.assert_allclose(
                    normals, expected.smooth_normals[expected.position_ids], atol=5e-5
                )
                assert repairs == 0
            elif case in ("high_degree", "opposed"):
                assert not np.any(normals)
                assert repairs == len(vertices)
            else:
                np.testing.assert_allclose(
                    normals[[0, 1, 2, 3, 4]], [[0, 0, 1]] * 5, atol=5e-5
                )
                assert repairs == 1
            assert sum(buffer.size for buffer in owned) == total
        finally:
            try:
                if not complete:
                    fence.wait()
            finally:
                memory.unpin("normal-test")
                memory.remove("normal-test")
                fence.close()
        assert memory.used == 0
