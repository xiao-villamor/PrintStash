"""Indexed GPU preparation retains its buffer leases until a completion fence.

The native API fake records uploads and lifetime, not shader execution. Physical
shader correctness is checked by the startup canary and optional GPU diagnostic.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRender

from app.modules.media.gpu_geometry import (
    CompletionFence,
    GeometryPacker,
    input_sizes,
    packed_chunks,
)
from app.runtime.compute.budget import Residency
from app.runtime.compute.contracts import ComputeUnavailable, Reason


class Buffer:
    def __init__(self, size, queue):
        self.queue = queue
        self.data = bytearray(size)
        self.destroyed = False

    def destroy(self):
        self.destroyed = True

    def map_sync(self, mode):
        assert not self.destroyed
        self.queue.fences += 1

    def unmap(self):
        assert not self.destroyed


class Queue:
    def __init__(self):
        self.submissions = 0
        self.fences = 0
        self.fail_submit = False

    def write_buffer(self, buffer, offset, data):
        incoming = memoryview(data).cast("B")
        assert not buffer.destroyed
        assert offset + len(incoming) <= len(buffer.data)
        buffer.data[offset : offset + len(incoming)] = incoming

    def submit(self, commands):
        self.submissions += len(commands)
        if self.fail_submit:
            raise RuntimeError("injected native submission failure")

    def on_submitted_work_done_sync(self):
        raise AssertionError("native callback completion is not portable")

    def read_buffer(self, buffer):
        self.fences += 1
        return memoryview(buffer.data)


class Pipeline:
    def get_bind_group_layout(self, index):
        return index


class ComputePass:
    def set_pipeline(self, pipeline):
        self.pipeline = pipeline

    def set_bind_group(self, index, group):
        self.group = group

    def dispatch_workgroups(self, *counts):
        self.count = counts

    def end(self):
        return None


class Encoder:
    def clear_buffer(self, buffer):
        assert not buffer.destroyed
        buffer.data[:] = bytes(len(buffer.data))

    def begin_compute_pass(self):
        return ComputePass()

    def finish(self):
        return self


class Device:
    def __init__(self):
        self.limits = {
            "max-storage-buffer-binding-size": 1024**2,
            "max-compute-workgroups-per-dimension": 65535,
        }
        self.queue = Queue()
        self.buffers = []
        self.fail_buffer_number = None

    def create_shader_module(self, *, code):
        return code

    def create_compute_pipeline(self, **kwargs):
        return Pipeline()

    def create_render_pipeline(self, **kwargs):
        return Pipeline()

    def create_buffer(self, *, size, usage):
        if len(self.buffers) == self.fail_buffer_number:
            raise MemoryError("injected native allocation failure")
        buffer = Buffer(size, self.queue)
        self.buffers.append(buffer)
        return buffer

    def create_bind_group(self, **kwargs):
        return kwargs

    def create_command_encoder(self):
        return Encoder()


@pytest.fixture
def device(monkeypatch):
    # GPU dependencies remain optional in the ordinary CPU test installation.
    # This fake owns only the native boundary; no shader math is emulated.
    monkeypatch.setitem(
        sys.modules,
        "wgpu",
        SimpleNamespace(
            BufferUsage=SimpleNamespace(
                STORAGE=128, COPY_DST=8, COPY_SRC=4, MAP_READ=1
            ),
            MapMode=SimpleNamespace(READ=1),
        ),
    )
    return Device()


@pytest.fixture
def prepared():
    faces = np.asarray([[0, 1, 2], [0, 0, 0]], dtype=np.int64)
    return PreparedRender(
        np.asarray([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float32),
        2,
        lambda size: iter([faces]),
        np.arange(3, dtype=np.int64),
        np.array([[[0, 0, 1]] * 3, [[0, 0, 0]] * 3], dtype=np.float32),
    )


class TestInputSizes:
    def test_charges_exact_compact_buffers(self, prepared):
        assert input_sizes(prepared) == (36, 24, 72)


class TestPackedChunks:
    def test_preserves_flat_face_output(self, prepared):
        packed = next(packed_chunks(prepared))

        assert packed.shape == (2, 6, 4)
        np.testing.assert_array_equal(packed[0, :3, :3], prepared.vertices)
        np.testing.assert_array_equal(packed[0, 3:, :3], np.tile([0, 0, 1], (3, 1)))
        np.testing.assert_array_equal(packed[1], np.zeros((6, 4)))
        assert packed[:, :, 3].tobytes() == bytes(48)


class TestCompletionFence:
    def test_ledger_release_invalidates_the_native_buffer(self, device):
        memory = Residency(64)
        fence = CompletionFence(device, memory)
        buffer = fence.buffer
        memory.unpin(fence.key)
        memory.remove(fence.key)

        assert fence.closed
        assert fence.buffer is None
        assert buffer.destroyed
        fence.close()
        with pytest.raises(RuntimeError, match="compute_geometry_fence_closed"):
            fence.wait()
        assert memory.used == 0


class TestGeometryPacker:
    def test_uploads_indexed_buffers_without_triangle_expansion(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        target = device.create_buffer(size=192, usage=128)

        packer.submit(prepared, target, "one")

        assert packer.transferred_bytes == 132
        assert packer.submissions == 1
        assert memory.used == 136
        np.testing.assert_array_equal(
            np.frombuffer(device.buffers[2].data, dtype=np.float32).reshape(3, 3),
            prepared.vertices,
        )
        np.testing.assert_array_equal(
            np.frombuffer(device.buffers[3].data, dtype=np.uint32), [0, 1, 2, 0, 0, 0]
        )
        np.testing.assert_array_equal(
            np.frombuffer(device.buffers[4].data, dtype=np.float32).reshape(2, 3, 3),
            prepared.corner_normals,
        )
        packer.release_completed(fence_complete=True)
        packer.close()
        assert memory.used == 0

    def test_protects_pending_inputs_from_eviction(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        packer.submit(prepared, device.create_buffer(size=192, usage=128), "one")

        with pytest.raises(ComputeUnavailable) as error:
            memory.make_room(256)

        assert error.value.reason is Reason.CAPACITY
        assert memory.used == 136
        assert all(not buffer.destroyed for buffer in device.buffers)
        packer.release_completed(fence_complete=True)
        packer.close()
        assert memory.used == 0

    def test_releases_inputs_after_existing_fence(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        packer.submit(prepared, device.create_buffer(size=192, usage=128), "one")

        packer.release_completed(fence_complete=True)

        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert packer.pending == []
        assert all(buffer.destroyed for buffer in device.buffers[2:])
        assert not device.buffers[1].destroyed
        assert device.queue.fences == 0

    def test_waits_once_on_exceptional_cleanup(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        packer.submit(prepared, device.create_buffer(size=192, usage=128), "one")

        # No native allocation is permitted after work has been queued.
        device.fail_buffer_number = len(device.buffers)
        packer.release_completed(fence_complete=False)
        packer.release_completed(fence_complete=False)

        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert device.queue.fences == 1
        assert all(buffer.destroyed for buffer in device.buffers[2:])

    def test_refuses_excess_capacity_before_allocating(self, device, prepared):
        memory = Residency(100)
        packer = GeometryPacker(device, memory)
        target = device.create_buffer(size=192, usage=128)

        with pytest.raises(ComputeUnavailable) as error:
            packer.submit(prepared, target, "one")

        assert error.value.reason is Reason.CAPACITY
        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert len(device.buffers) == 2
        assert device.buffers[-1] is target

    def test_refuses_device_storage_limit_before_allocating(self, device, prepared):
        device.limits["max-storage-buffer-binding-size"] = 16
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        target = device.create_buffer(size=192, usage=128)

        with pytest.raises(ComputeUnavailable) as error:
            packer.submit(prepared, target, "one")

        assert error.value.reason is Reason.CAPACITY
        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert len(device.buffers) == 2
        assert device.buffers[-1] is target

    def test_releases_partial_allocations_after_failure(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        target = device.create_buffer(size=192, usage=128)
        device.fail_buffer_number = 3

        with pytest.raises(MemoryError, match="injected native allocation failure"):
            packer.submit(prepared, target, "one")
        packer.release_completed(fence_complete=False)

        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert all(buffer.destroyed for buffer in device.buffers[2:])
        assert device.queue.fences == 1

    def test_retains_inputs_after_failed_submission(self, device, prepared):
        memory = Residency(256)
        packer = GeometryPacker(device, memory)
        target = device.create_buffer(size=192, usage=128)
        device.queue.fail_submit = True

        with pytest.raises(RuntimeError, match="injected native submission failure"):
            packer.submit(prepared, target, "one")

        assert memory.used == 136
        assert all(not buffer.destroyed for buffer in device.buffers)
        device.queue.fail_submit = False
        packer.release_completed(fence_complete=False)
        assert memory.used == 4
        packer.close()
        assert memory.used == 0

    def test_refuses_canary_output_mismatch(self, device):
        memory = Residency(1024**2)
        packer = GeometryPacker(device, memory)

        with pytest.raises(ValueError, match="compute_geometry_packing_canary"):
            packer.canary()

        assert memory.used == 4
        packer.close()
        assert memory.used == 0
        assert packer.pending == []
        assert all(buffer.destroyed for buffer in device.buffers)


class TestRendererUpload:
    def test_small_models_share_the_compact_path(self, device, prepared):
        from app.modules.media.webgpu_render import Renderer

        memory = Residency(1024**2)
        renderer = Renderer(device, memory, packing_min_faces=3)
        result = renderer.upload(prepared, "geometry:small")

        assert len(result.positions.data) == 36
        assert len(result.faces.data) == 24
        assert renderer.gpu_geometry_uploads == 1
        assert renderer.cpu_geometry_uploads == 0
        assert renderer.packer is not None
        assert memory.entries["geometry:small"].size == 164
        renderer.close()
        assert memory.used == 0

    def test_reuses_the_same_compact_allocation(self, device, prepared):
        from app.modules.media.webgpu_render import Renderer

        memory = Residency(1024**2)
        renderer = Renderer(device, memory)
        first = renderer.upload(prepared, "geometry:one")
        second = renderer.upload(prepared, "geometry:one")

        assert first is second
        assert renderer.uploads == renderer.cache_hits == 1
        assert len(renderer.packer.pending) == 1
        renderer.close()
        assert memory.used == 0

    def test_reopens_owner_after_renderer_workspace_eviction(self, device, prepared):
        from app.modules.media.webgpu_render import Renderer

        memory = Residency(1024**2)
        renderer = Renderer(device, memory)
        renderer.upload(prepared, "geometry:first")
        first = renderer.packer
        assert first is not None

        renderer.close()
        assert first.fence.closed
        assert renderer.packer is None
        renderer.upload(prepared, "geometry:second")

        assert renderer.gpu_geometry_uploads == 2
        assert renderer.packer is not None
        assert renderer.packer is not first
        assert not renderer.packer.fence.closed
        renderer.close()
        assert memory.used == 0
        assert device.queue.fences == 2
