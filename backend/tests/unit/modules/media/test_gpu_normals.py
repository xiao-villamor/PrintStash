"""Deferred normals retain canonical fallback and bounded deterministic topology."""

from types import SimpleNamespace

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import (
    PreparedRenderGeometry,
    prepare_mesh,
    prepare_mesh_render,
)

from app.modules.media.gpu_normals import (
    MAX_DEGREE,
    DeferredRender,
    NormalPipeline,
    build_csr,
    canonical,
    defer,
    device_input_bytes,
    host_scratch_bytes,
)


def source():
    return SimpleNamespace(
        vertices=np.asarray([[0.0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 0]]),
        faces=np.asarray([[0, 1, 2], [3, 2, 1]], dtype=np.int64),
    )


def deferred():
    return defer(prepare_mesh(source()))


class TestCanonical:
    def test_preserves_canonical_fallback(self):
        original = source()
        prepared = defer(prepare_mesh(original))
        original.vertices[:] += 500
        original.faces[:] = 0
        result = canonical(prepared)
        expected = prepare_mesh_render(source())
        np.testing.assert_array_equal(result.position_ids, expected.position_ids)
        np.testing.assert_array_equal(result.smooth_normals, expected.smooth_normals)
        assert not hasattr(prepared, "smooth_normals")
        assert result.vertices is prepared.vertices
        assert not result.smooth_normals.flags.writeable

    def test_broker_fallback_does_not_grow_cached_descriptor(self):
        prepared = deferred()
        first, second = canonical(prepared), canonical(prepared)
        assert first is not second
        assert prepared._canonical is None
        assert canonical(first) is first

    def test_parser_memoization_requires_explicit_opt_in(self):
        prepared = deferred()
        result = canonical(prepared, cache=True)
        assert canonical(prepared, cache=True) is result
        assert canonical(prepared, cache=False) is not result


class TestBuildCSR:
    def test_preserves_original_corner_order_with_duplicate_positions(self):
        prepared = deferred()
        plan = build_csr(prepared)
        expected = prepared.position_ids[plan.faces].ravel()
        np.testing.assert_array_equal(np.sort(plan.corners), np.arange(6))
        for identity in range(prepared.position_count):
            actual = plan.corners[plan.offsets[identity] : plan.offsets[identity + 1]]
            np.testing.assert_array_equal(actual, np.flatnonzero(expected == identity))
        assert all(
            not array.flags.writeable
            for array in (plan.faces, plan.offsets, plan.corners)
        )

    def test_rejects_coordinates_outside_finite_gpu_arithmetic(self):
        prepared = deferred()
        vertices = prepared.vertices * 1e12
        geometry = PreparedRenderGeometry(
            vertices, prepared.face_count, prepared.face_chunks
        )
        invalid = DeferredRender(
            geometry, prepared.position_ids, prepared.position_count
        )
        with pytest.raises(ValueError, match="compute_normals_numeric_range"):
            build_csr(invalid)

    def test_tiny_face_preserves_nonzero_canonical_normal(self):
        vertices = np.asarray(
            [[0, 0, 0], [1e-20, 0, 0], [0, 1e-20, 0]], dtype=np.float32
        )
        faces = np.asarray([[0, 1, 2]], dtype=np.int64)
        prepared = defer(
            PreparedRenderGeometry(vertices, len(faces), lambda size: iter([faces]))
        )
        np.testing.assert_array_equal(
            canonical(prepared).smooth_normals, [[0, 0, 1]] * 3
        )

    def test_refuses_unbounded_valence_before_sort(self, monkeypatch):
        mesh = source()
        mesh.faces = np.tile([[0, 1, 2]], (MAX_DEGREE + 1, 1))
        prepared = defer(prepare_mesh(mesh))
        monkeypatch.setattr(
            np, "argsort", lambda *_args, **_kwargs: pytest.fail("unbounded sort")
        )
        with pytest.raises(ValueError, match="compute_normals_valence"):
            build_csr(prepared)

    @pytest.mark.parametrize(
        "faces,count",
        [
            ([[0, 1, 99]], 1),
            ([[0, -1, 2]], 1),
            ([[0, 1, 2]], 2),
            ([[0, 1, 2], [0, 1, 2]], 1),
        ],
    )
    def test_rejects_invalid_or_inconsistent_chunks(self, faces, count):
        prepared = deferred()
        indices = np.asarray(faces, dtype=np.int64)
        geometry = PreparedRenderGeometry(
            prepared.vertices, count, lambda size: iter([indices])
        )
        invalid = DeferredRender(
            geometry, prepared.position_ids, prepared.position_count
        )
        with pytest.raises(ValueError, match="compute_normals_"):
            build_csr(invalid)


class Buffer:
    def __init__(self, size):
        self.size = size
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class Device:
    limits = {
        "max-storage-buffer-binding-size": 128 * 1024**2,
        "max-compute-workgroups-per-dimension": 65535,
    }

    def __init__(self, fail_allocation=0, fail_write=False):
        self.buffers = []
        self.fail_allocation = fail_allocation
        self.fail_write = fail_write
        self.queue = SimpleNamespace(write_buffer=self.write_buffer)

    def create_buffer(self, *, size, usage):
        if len(self.buffers) + 1 == self.fail_allocation:
            raise RuntimeError("allocation failed")
        result = Buffer(size)
        self.buffers.append(result)
        return result

    def write_buffer(self, *_args):
        if self.fail_write:
            raise RuntimeError("write failed")

    def create_shader_module(self, **_kwargs):
        return object()

    def create_compute_pipeline(self, **_kwargs):
        return SimpleNamespace(get_bind_group_layout=lambda index: None)

    def create_bind_group(self, **_kwargs):
        return object()


class Encoder:
    def __init__(self):
        self.passes = 0

    def clear_buffer(self, _buffer):
        pass

    def begin_compute_pass(self):
        self.passes += 1
        return SimpleNamespace(
            set_pipeline=lambda *_: None,
            set_bind_group=lambda *_: None,
            dispatch_workgroups=lambda *_: None,
            end=lambda: None,
        )


class TestNormalPipeline:
    def test_accounts_for_all_normal_allocations(self):
        plan = build_csr(deferred())
        device = Device()
        pipeline = NormalPipeline(device)
        owned = []
        encoder = Encoder()
        result = pipeline.encode(
            plan, encoder, positions=object(), faces=object(), owned=owned
        )
        shared = (
            plan.prepared.vertices.nbytes
            + plan.faces.nbytes
            + len(plan.prepared.position_ids) * 4
        )
        assert shared + sum(buffer.size for buffer in owned) == device_input_bytes(
            plan.prepared
        )
        assert result.status.size == 4
        assert result.smooth.size == plan.n_positions * 12
        assert encoder.passes == 2
        assert host_scratch_bytes(plan.prepared) > sum(
            array.nbytes for array in (plan.faces, plan.offsets, plan.corners)
        )

    @pytest.mark.parametrize(
        "fail_allocation,fail_write,retained", [(3, False, 2), (0, True, 5)]
    )
    def test_partial_failures_keep_every_allocation_with_callers_fence(
        self, fail_allocation, fail_write, retained
    ):
        plan = build_csr(deferred())
        device = Device(fail_allocation, fail_write)
        pipeline = NormalPipeline(device)
        owned = []
        with pytest.raises(RuntimeError, match="failed"):
            pipeline.encode(
                plan, Encoder(), positions=object(), faces=object(), owned=owned
            )
        assert len(owned) == retained
        assert owned == device.buffers
        assert not any(buffer.destroyed for buffer in owned)
