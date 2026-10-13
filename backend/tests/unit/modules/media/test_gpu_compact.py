"""Compact buffer accounting and native command ownership, without a GPU."""

import struct
import sys

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRender, PreparedRenderGeometry

from app.modules.media.gpu_compact import CompactGeometry
from app.modules.media.gpu_normals import VisualRender
from app.modules.media.gpu_render_layout import device_geometry_bytes, geometry_sizes
from app.modules.media.gpu_views import ViewPipeline, frame_config
from app.modules.media.gpu_visual_normals import VisualNormalPipeline
from app.modules.media.webgpu_render import Renderer
from app.runtime.compute.budget import Residency
from tests.fakes.gpu_device import API, Device


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setitem(sys.modules, "wgpu", API)
    return Device()


@pytest.fixture
def prepared():
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    return PreparedRender(
        np.asarray([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float32),
        1,
        lambda size: iter([faces]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )


class TestCompactGeometry:
    def test_retains_grouped_uploads_until_completion(self, device, prepared):
        memory = Residency(1024**2)
        owner = CompactGeometry(device, memory, ViewPipeline(device))
        first = owner.acquire(prepared, "geometry:first")
        second = owner.acquire(prepared, "geometry:second")
        configs = device.create_buffer(size=512, usage=64)
        encoder = device.create_command_encoder()
        owner.encode(encoder, configs, frame_config(None, 100, 100, False) * 2)
        staging = list(owner.staging)
        assert device.queue.writes == 0
        assert device.mapped_writes == 1
        assert memory.entries["geometry:first"].pins == 1
        assert not any(buffer.destroyed for buffer in staging)
        device.queue.submit([encoder.finish()])
        assert first.positions.data == prepared.vertices.tobytes()
        assert second.faces.data == np.asarray([[0, 1, 2]], dtype="<u4").tobytes()
        owner.release_completed(fence_complete=True)
        assert all(buffer.destroyed for buffer in staging)
        assert not first.positions.destroyed
        assert memory.used == 2 * sum(geometry_sizes(3, 1, 3)) + 4
        owner.close()
        memory.evict_idle()
        assert memory.used == 0

    def test_grouped_upload_splits_at_native_buffer_limit(self, device, prepared):
        device.limits["max-buffer-size"] = 64
        memory = Residency(1024**2)
        owner = CompactGeometry(device, memory, ViewPipeline(device))
        geometry = owner.acquire(prepared, "geometry:split")
        configs = device.create_buffer(size=256, usage=64)
        encoder = device.create_command_encoder()
        expected = frame_config(None, 100, 100, False)
        owner.encode(encoder, configs, expected)
        assert len(owner.staging) == 6
        assert all(len(buffer.data) <= 64 for buffer in owner.staging)
        device.queue.submit([encoder.finish()])
        assert geometry.positions.data == prepared.vertices.tobytes()
        assert configs.data == expected
        assert device.queue.submissions == 1
        owner.release_completed(fence_complete=True)
        owner.close()
        memory.evict_idle()
        assert memory.used == 0

    def test_visual_upload_has_no_cpu_identities_or_normals(self, device, prepared):
        visual = VisualRender(
            PreparedRenderGeometry(prepared.vertices, 1, prepared.face_chunks)
        )
        memory = Residency(1024**2)
        owner = CompactGeometry(device, memory, ViewPipeline(device))
        # Inject a qualified provider at the native API boundary. This fake
        # records commands and cannot execute a physical shader canary.
        owner.visual_normals = VisualNormalPipeline(device)
        geometry = owner.acquire(visual, "geometry:visual")
        configs = device.create_buffer(size=256, usage=64)
        encoder = device.create_command_encoder()
        owner.encode(encoder, configs, frame_config(None, 100, 100, False))
        assert owner.transferred_bytes == 36 + 12 + 256
        assert owner.upload_calls == 1
        assert owner.telemetry_keys() == {"geometry:visual"}
        assert set(owner.normal_statuses()) == {"geometry:visual"}
        assert len(geometry.identities.data) == 12
        assert memory.used == device_geometry_bytes(3, 1, 3, "gpu-visual-v2") + 256 + 4
        device.queue.submit([encoder.finish()])
        owner.release_completed(fence_complete=True)
        owner.close()
        memory.evict_idle()
        assert memory.used == 0

    def test_retires_partial_allocations_after_completion(self, device, prepared):
        memory = Residency(1024**2)
        renderer = Renderer(device, memory)
        # The completion fence succeeds, then a partial geometry allocation fails.
        device.fail_allocation = 3
        with pytest.raises(MemoryError):
            renderer.upload(prepared, "geometry:failed")
        renderer.close()
        assert memory.used == 0
        assert all(buffer.destroyed for buffer in device.buffers)
        assert device.queue.maps == 1


class TestNativeBatch:
    def test_coalesces_multiple_models_into_one_native_batch(self, device, prepared):
        renderer = Renderer(device)
        decoded = [
            (prepared, 24, 24, [None, np.eye(3)], False),
            (prepared, 24, 24, [np.eye(3)], True),
        ]
        outputs = renderer.execute_prepared_many(
            decoded, ["geometry:a", "geometry:b"], ["a", "b"], raw=True
        )
        assert [struct.unpack("!III", output[:12]) for output in outputs] == [
            (48, 48, 2),
            (48, 48, 1),
        ]
        assert device.draws == [(3, 1)] * 3
        assert device.queue.submissions == 1
        assert device.queue.maps == 1
        assert device.queue.writes == 0
        assert device.mapped_writes == 1
        assert renderer.bounds_vertices == 6
        assert renderer.submissions == renderer.readback_batches == 1
        assert renderer.batch_model_counts[1] == renderer.batch_frame_counts[2] == 1
        camera_offsets = [
            group["entries"][1]["resource"]["offset"]
            for group in device.groups
            if len(group["entries"]) == 3
        ]
        assert camera_offsets == [0, 256, 512]
        assert len({slot["offset"] for slot in device.readbacks}) == 3
        assert not renderer.unverified_normals
        renderer.close()

    def test_render_does_not_rotate_or_reduce_full_vertices_on_cpu(
        self, device, prepared
    ):
        class GpuOnlyVertices(np.ndarray):
            def __matmul__(self, other):
                raise AssertionError("visual projection belongs to the GPU")

            def min(self, *args, **kwargs):
                raise AssertionError("visual bounds belong to the GPU")

            def max(self, *args, **kwargs):
                raise AssertionError("visual bounds belong to the GPU")

        vertices = prepared.vertices.view(GpuOnlyVertices)
        guarded = PreparedRender(
            vertices,
            prepared.face_count,
            prepared.face_chunks,
            prepared.position_ids,
            prepared.smooth_normals,
        )
        before = vertices.tobytes()
        renderer = Renderer(device)
        outputs = renderer.execute_prepared_many(
            [(guarded, 24, 24, [None, np.eye(3)], False)],
            ["geometry:gpu-bounds"],
            ["bounds"],
            raw=True,
        )
        assert len(outputs) == 1
        assert vertices.tobytes() == before
        assert renderer.bounds_vertices == len(vertices)
        renderer.close()

    def test_reuses_prepared_geometry_for_another_camera(self, device, prepared):
        renderer = Renderer(device)
        args = [(prepared, 24, 24, [None], False)]
        renderer.execute_prepared_many(args, ["geometry:a"], ["first"], raw=True)
        renderer.execute_prepared_many(args, ["geometry:a"], ["second"], raw=True)
        assert renderer.uploads == 1
        assert renderer.cache_hits == 1
        assert renderer.bounds_vertices == 3
        assert renderer.submissions == 2
        assert renderer.transfers == 36 + 12 + 12 + 36 + 2 * 256
        renderer.close()

    @pytest.mark.parametrize("failure", ["submit", "map", "upload"])
    def test_failure_retires_unverified_geometry_after_completion(
        self, device, prepared, failure
    ):
        renderer = Renderer(device)
        if failure == "submit":
            device.queue.fail_submit = True
        elif failure == "map":
            device.fail_map = True
        else:
            device.fail_write_mapped = True
        with pytest.raises(RuntimeError, match="injected"):
            renderer.execute_prepared_many(
                [(prepared, 24, 24, [None], False)], ["geometry:a"], ["a"], raw=True
            )
        assert renderer.geometry == {}
        assert renderer.unverified_normals == set()
        assert renderer.packer.pending == []
        assert renderer.packer.staging is None
        assert device.queue.maps >= 1
        assert all(
            entry.pins == 0
            for key, entry in renderer.memory.entries.items()
            if not key.startswith("packing-fence:")
        )
        renderer.close()

    def test_repeated_output_does_not_upload_submit_or_map(self, device, prepared):
        renderer = Renderer(device)
        args = [(prepared, 24, 24, [None], False)]
        expected = renderer.execute_prepared_many(args, ["geometry:a"], ["a"], raw=True)
        actual = renderer.execute_prepared_many(args, ["geometry:a"], ["a"], raw=True)
        assert actual == expected
        assert renderer.output_hits == 1
        assert (
            device.queue.maps == device.queue.submissions == device.mapped_writes == 1
        )
        assert device.queue.writes == 0
        renderer.close()
