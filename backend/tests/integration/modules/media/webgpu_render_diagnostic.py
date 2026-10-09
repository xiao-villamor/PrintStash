"""Real optional WebGPU diagnostic; software results never qualify acceleration."""

import struct

import numpy as np
import pytest
from printstash_core.mesh.rasterizer import render_prepared_pixels
from printstash_core.mesh.render_geometry import prepare_mesh_render

from app.modules.media.compute_geometry import encode
from app.modules.media.webgpu_render import Renderer
from app.runtime.compute.contracts import ComputeUnavailable, Reason


@pytest.fixture
def renderer():
    import wgpu
    adapters = wgpu.gpu.enumerate_adapters_sync()
    assert adapters, "Install the optional GPU extra and expose a WebGPU adapter"
    device = adapters[0].request_device_sync()
    owner = Renderer(device)
    try:
        yield owner
    finally:
        owner.close()
        for key in list(owner.memory.entries):
            owner.memory.remove(key)
        device.destroy()


class TestWebGpuRender:
    @pytest.mark.parametrize("shape", ["box", "sphere"])
    def test_reuses_uploaded_geometry_for_repeated_views(self, renderer, shape):
        import trimesh

        mesh = (
            trimesh.creation.box()
            if shape == "box"
            else trimesh.creation.icosphere(subdivisions=2)
        )
        prepared = prepare_mesh_render(mesh)
        payload = encode(prepared, 64, 64, [None, None], False)
        expected = render_prepared_pixels(prepared, "", 64, 64)

        first = renderer.execute(payload)
        transferred = renderer.transfers
        second = renderer.execute(payload)

        size = 64 * 64 * 4
        assert struct.unpack("!III", first[:12]) == (64, 64, 2)
        assert first[12 : 12 + size] == first[12 + size :]
        assert second == first
        assert renderer.transfers == transferred == prepared.face_count * 96
        assert renderer.cache_hits == 1
        actual = np.frombuffer(first[12 : 12 + size], dtype=np.uint8).reshape(-1, 4)
        baseline = np.frombuffer(expected.rgba, dtype=np.uint8).reshape(-1, 4)
        assert np.array_equal(actual[:, 3] >= 128, baseline[:, 3] >= 128)
        assert np.abs(actual.astype(int) - baseline.astype(int)).max() <= 8

    def test_preserves_shared_edges_in_orthographic_views(self, renderer):
        import trimesh

        from app.modules.media.geometry_analysis import canonical_frames

        prepared = prepare_mesh_render(trimesh.creation.box())
        views = [np.asarray(view) for view in canonical_frames()]
        payload = encode(prepared, 224, 224, views, True)
        actual = renderer.execute(payload)
        size = 224 * 224 * 4

        for index, view in enumerate(views):
            expected = render_prepared_pixels(
                prepared, "", 224, 224, view_rotation=view, matte=True
            )
            candidate = np.frombuffer(
                actual[12 + index * size : 12 + (index + 1) * size], dtype=np.uint8
            ).reshape(-1, 4)
            baseline = np.frombuffer(expected.rgba, dtype=np.uint8).reshape(-1, 4)
            assert np.array_equal(candidate[:, 3] >= 128, baseline[:, 3] >= 128)
            assert np.abs(candidate.astype(int) - baseline.astype(int)).max() <= 8

    def test_refuses_workspace_growth_over_budget(self, renderer):
        renderer.workspace_limit = 1

        with pytest.raises(ComputeUnavailable) as exc:
            renderer.attachments(64, 64, 100)

        assert exc.value.reason == Reason.CAPACITY
        assert renderer.color is None
