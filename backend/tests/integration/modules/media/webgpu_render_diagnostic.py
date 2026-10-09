"""Real optional WebGPU diagnostic; software results never qualify acceleration."""

import struct

import numpy as np
import pytest
from printstash_core.mesh.rasterizer import render_prepared_pixels
from printstash_core.mesh.render_geometry import prepare_mesh_render

from app.modules.media.compute_geometry import encode
from app.modules.media.webgpu_render import Renderer
from app.runtime.compute.contracts import ComputeUnavailable, Reason


@pytest.fixture(scope="session")
def gpu_device():
    import wgpu

    from app.runtime.compute.discovery import configure_backend

    configure_backend()
    adapters = wgpu.gpu.enumerate_adapters_sync()
    assert adapters, "Install the optional GPU extra and expose a WebGPU adapter"
    device = adapters[0].request_device_sync()
    yield device
    device.destroy()


@pytest.fixture
def renderer(gpu_device):
    owner = Renderer(gpu_device)
    try:
        yield owner
    finally:
        owner.close()
        for key in list(owner.memory.entries):
            owner.memory.remove(key)


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
        assert renderer.output_hits == 1
        assert renderer.readback_batches == 1
        assert renderer.submissions == 2
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

    def test_batches_distinct_models_under_one_readback_fence(self, renderer):
        import trimesh

        box = prepare_mesh_render(trimesh.creation.box())
        sphere = prepare_mesh_render(trimesh.creation.icosphere(subdivisions=2))
        payloads = [encode(p, 64, 64, [None], False) for p in [box, sphere]]
        outputs = renderer.execute_many(payloads)
        assert len(outputs) == 2
        assert outputs[0] != outputs[1]
        assert renderer.readback_batches == 1
        assert renderer.submissions == 2
        assert renderer.uploads == 2
        assert len(renderer.geometry) == 2
        renderer.outputs.clear()
        renderer.output_bytes = 0
        assert [renderer.execute(p) for p in payloads] == outputs
        assert renderer.uploads == 2
        assert renderer.cache_hits == 2

    def test_rejects_oversized_camera_batch(self, renderer):
        import trimesh

        prepared = prepare_mesh_render(trimesh.creation.box())
        payload = encode(prepared, 64, 64, [None] * 5, False)
        with pytest.raises(ComputeUnavailable) as exc:
            renderer.execute_many([payload, payload])
        assert exc.value.reason is Reason.CAPACITY
        assert renderer.submissions == 0

    def test_evicts_shaded_outputs_at_the_host_cache_ceiling(
        self, renderer, monkeypatch
    ):
        import trimesh

        from app.modules.media import webgpu_render

        monkeypatch.setattr(webgpu_render, "OUTPUT_CACHE_BYTES", 20_000)
        prepared = prepare_mesh_render(trimesh.creation.box())
        left = encode(prepared, 64, 64, [None], False)
        right = encode(prepared, 64, 64, [None], True)
        renderer.execute_many([left, right])
        assert renderer.output_bytes <= 20_000
        assert len(renderer.outputs) == 1
        assert renderer.uploads == 1
        renderer.execute(left)
        assert renderer.output_hits == 0


class TestWarmPoolAdmission:
    def test_batches_small_models_after_a_large_singleton(
        self, monkeypatch, tmp_path, gpu_device
    ):
        import base64
        import time

        import trimesh
        import wgpu
        from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE

        from app.core.config import _overlay
        from app.modules.media.compute_render import RECIPE
        from app.runtime.compute import dispatcher
        from app.runtime.compute.contracts import ComputeMode, DeviceInfo
        from app.runtime.compute.protocol import Priority, RenderRequest

        device = wgpu.gpu.enumerate_adapters_sync()[0].request_device_sync()
        info = DeviceInfo(
            identity="diagnostic",
            name="diagnostic",
            vendor_id=1,
            device_id=1,
            driver="diagnostic",
            backend="OpenGL",
        )
        monkeypatch.setattr(dispatcher, "discover", lambda _: (info, device))
        _overlay["compute_render_policy"] = "preview"
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        owner.host_capacity = 1024**3
        monkeypatch.setattr(owner, "admit_host", lambda _: None)

        def request(mesh, identity):
            prepared = prepare_mesh_render(mesh)
            return RenderRequest(
                deadline=time.monotonic() + 30,
                priority=Priority.BACKGROUND,
                request_id=identity,
                recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:64x64:1:0",
                units=prepared.face_count,
                payload=base64.b64encode(
                    encode(prepared, 64, 64, [None], False)
                ).decode(),
            )

        try:
            large = request(trimesh.creation.icosphere(subdivisions=4), "large")
            small = request(trimesh.creation.box(), "small")
            other = request(trimesh.creation.icosphere(subdivisions=1), "other")
            owner.render(large)
            renderer = owner.renderer
            uploads = renderer.uploads
            outputs = owner.execute_render_batch([small, other])
            assert len(outputs) == 2
            assert outputs[0] != outputs[1]
            assert owner.renderer is renderer
            assert renderer.readback_batches == 2
            assert renderer.submissions == 3
            assert renderer.uploads == uploads + 2
            assert owner.memory.used <= owner.memory.capacity
        finally:
            owner.close()
