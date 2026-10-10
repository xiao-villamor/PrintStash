"""Native-boundary conformance, without claiming physical GPU qualification."""

import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.render_backend import GpuError, GpuFailure
from scripts.wgpu_render_backend import (
    GpuContext,
    GpuFrame,
    _Device,
    _Frame,
    _native_device,
    is_hardware,
)


class NativeFrame:
    def __init__(self, owner, width, height):
        self.owner = owner
        self.payload = np.zeros((height, width, 4), dtype=np.float32)
        self.payload[0, 0] = [0, 0, 1, 1]
        self.close_count = 0
        self.uploads = []
        self.draws = []

    def upload(self, payload):
        if self.owner.failure is not None:
            raise self.owner.failure
        self.uploads.append(payload)

    def draw(self, vertices):
        self.draws.append(vertices)

    def read(self):
        return self.payload.tobytes()

    def close(self):
        self.close_count += 1


class NativeDevice:
    def __init__(self):
        self.info = {
            "adapter_type": "IntegratedGPU",
            "device": "arbitrary vendor",
            "backend_type": "Vulkan",
        }
        self.limits = {"max-texture-dimension-2d": 4096, "max-buffer-size": 2**24}
        self.frames = []
        self.close_count = 0
        self.failure = None

    def frame(self, width, height, chunk, radius):
        frame = NativeFrame(self, width, height)
        self.frames.append(frame)
        return frame

    def close(self):
        self.close_count += 1


@pytest.fixture
def session():
    device = NativeDevice()
    context = GpuContext.create(context_factory=lambda selector, software: device)
    yield device, context
    context.close()


@pytest.fixture
def inputs():
    return (
        np.zeros((2, 2, 3), dtype=np.uint8),
        np.full((2, 2), np.inf),
        np.array([[[0.1, 0.1, 0], [1.5, 0.1, 0], [0.1, 1.5, 0]]]),
        np.broadcast_to(np.array([0.0, 0.0, 1.0]), (1, 3, 3)),
        lambda n: n,
        np.full(3, 255.0),
        2,
        2,
    )


class TestAdapterEligibility:
    @pytest.mark.parametrize("vendor", ["NVIDIA", "Intel", "AMD", "unlisted vendor"])
    def test_ignores_vendor_identity(self, vendor):
        assert is_hardware({"adapter_type": "IntegratedGPU", "vendor": vendor})

    @pytest.mark.parametrize("kind", ["CPU", "Unknown"])
    def test_refuses_software_acceleration_claims(self, kind):
        assert not is_hardware({"adapter_type": kind})


class TestGpuContext:
    def test_closes_owned_resources_once(self, session):
        device, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        context.close()
        context.close()
        assert device.close_count == 1
        assert frame.native.close_count == 1

    def test_refuses_cross_thread_access(self, session):
        _, context = session
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(context.require_open)
            with pytest.raises(GpuError) as failure:
                future.result()
        assert failure.value.reason is GpuFailure.CLOSED
        context.require_open()

    def test_preserves_dependency_failure(self):
        def absent(selector, software):
            raise ImportError("wgpu not installed")

        with pytest.raises(GpuError) as failure:
            GpuContext.create(context_factory=absent)
        assert failure.value.reason is GpuFailure.DEPENDENCY_UNAVAILABLE

    def test_preserves_capability_refusal(self):
        def unavailable(selector, software):
            raise GpuError(GpuFailure.CAPABILITY_UNAVAILABLE)

        with pytest.raises(GpuError) as failure:
            GpuContext.create(context_factory=unavailable)
        assert failure.value.reason is GpuFailure.CAPABILITY_UNAVAILABLE


class TestGpuFrame:
    @pytest.mark.parametrize(
        "radius", [0.0, -1.0, float("nan"), float("inf"), 1e-300, 1e300, 10**400]
    )
    def test_refuses_depth_not_representable_by_gpu(self, session, radius):
        device, context = session
        with pytest.raises(GpuError) as failure:
            GpuFrame(context, 2, 2, 1, radius)
        assert failure.value.reason is GpuFailure.INVALID_REQUEST
        assert not device.frames

    def test_shades_complete_readback_with_canonical_callback(self, session, inputs):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        frame.finish(*inputs[:2])
        np.testing.assert_array_equal(inputs[0][0, 0], [0, 0, 255])
        assert np.isfinite(inputs[1][0, 0])
        assert np.isinf(inputs[1][1, 0])
        assert frame.stats.readback_count == 1

    def test_defers_publication_until_finish(self, session, inputs):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        frame(*inputs)
        assert not inputs[0].any()
        assert np.isinf(inputs[1]).all()
        assert frame.native.draws == [3, 3]
        assert frame.stats.upload_bytes == 144

    def test_refuses_excess_allocation_before_native_work(self, session):
        device, context = session
        with pytest.raises(GpuError) as failure:
            GpuFrame(context, 4096, 4096, 1, 2, allocation_limit_bytes=1024)
        assert failure.value.reason is GpuFailure.ALLOCATION_FAILED
        assert not device.frames

    def test_accepts_exact_allocation_limit(self, session):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2, allocation_limit_bytes=680)
        assert frame.stats.requested_allocation_bytes == 680

    def test_refuses_device_texture_limit(self, session):
        device, context = session
        device.limits["max-texture-dimension-2d"] = 1
        with pytest.raises(GpuError) as failure:
            GpuFrame(context, 2, 2, 1, 2)
        assert failure.value.reason is GpuFailure.CAPABILITY_UNAVAILABLE
        assert not device.frames

    def test_refuses_competing_frame(self, session):
        device, context = session
        GpuFrame(context, 2, 2, 1, 2)
        with pytest.raises(GpuError) as failure:
            GpuFrame(context, 2, 2, 1, 2)
        assert failure.value.reason is GpuFailure.INVALID_REQUEST
        assert len(device.frames) == 1

    @pytest.mark.parametrize(
        "payload", [np.zeros((1, 4), dtype=np.float32), np.full((2, 2, 4), np.nan)]
    )
    def test_refuses_invalid_readback_without_publication(
        self, session, inputs, payload
    ):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        frame.native.payload = payload
        with pytest.raises(GpuError) as failure:
            frame.finish(*inputs[:2])
        assert failure.value.reason is GpuFailure.READBACK_FAILED
        assert not inputs[0].any()
        assert np.isinf(inputs[1]).all()

    def test_recovers_session_after_device_failure(self, session, inputs):
        device, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        device.failure = GpuError(GpuFailure.DEVICE_LOST)
        with pytest.raises(GpuError) as failure:
            frame(*inputs)
        assert failure.value.reason is GpuFailure.DEVICE_LOST
        frame.close()
        assert frame.native.close_count == 1
        context.close()
        fresh = GpuContext.create(context_factory=lambda s, a: NativeDevice())
        try:
            recovered = GpuFrame(fresh, 2, 2, 1, 2)
            recovered(*inputs)
            recovered.finish(*inputs[:2])
            assert inputs[0].any()
        finally:
            fresh.close()

    def test_refuses_repeat_publication(self, session, inputs):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        frame.finish(*inputs[:2])
        with pytest.raises(GpuError) as failure:
            frame.finish(*inputs[:2])
        assert failure.value.reason is GpuFailure.CLOSED
        assert frame.stats.readback_count == 1

    def test_matches_cpu_quantization_from_original_precision(self, session, inputs):
        from printstash_core.mesh.rasterizer import _rasterise_triangles

        _, context = session
        values = list(inputs)
        values[3] = np.array(
            [
                [
                    [0.234567891234, 0.2, 0.91],
                    [0.765432109876, 0.3, 0.63],
                    [0.456789123456, 0.7, 0.53],
                ]
            ]
        )
        expected = values[0].copy()
        _rasterise_triangles(expected, values[1].copy(), *values[2:])
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*values)
        frame.finish(*values[:2])
        np.testing.assert_array_equal(values[0][0, 0], expected[0, 0])

    def test_keeps_submitted_geometry_owned_until_readback(self, session, inputs):
        _, context = session
        values = list(inputs)
        values[3] = inputs[3].copy()
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*values)
        values[2][:] = 100
        values[3][:] = 0
        frame.finish(*values[:2])
        np.testing.assert_array_equal(values[0][0, 0], [0, 0, 255])

    def test_resolves_winner_from_later_chunk(self, session, inputs):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        second = list(inputs)
        second[4] = lambda n: np.broadcast_to([0.0, 1.0, 0.0], n.shape)
        frame(*second)
        frame.native.payload[0, 0, 3] = 2
        frame.finish(*inputs[:2])
        np.testing.assert_array_equal(inputs[0][0, 0], [0, 255, 0])

    @pytest.mark.parametrize("face_id", [-1, 0.5, 2, float("inf")])
    def test_rejects_invalid_winning_face_without_publication(
        self, session, inputs, face_id
    ):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        frame.native.payload[0, 0, 3] = face_id
        with pytest.raises(GpuError) as failure:
            frame.finish(*inputs[:2])
        assert failure.value.reason is GpuFailure.READBACK_FAILED
        assert not inputs[0].any()

    def test_bounds_retained_host_geometry_before_upload(self, session, inputs):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2, retained_geometry_limit_bytes=144)
        frame(*inputs)
        with pytest.raises(GpuError) as failure:
            frame(*inputs)
        assert failure.value.reason is GpuFailure.ALLOCATION_FAILED
        assert len(frame.native.uploads) == 1
        assert frame.stats.retained_geometry_bytes == 144

    def test_releases_retained_geometry_on_close(self, session, inputs):
        import weakref

        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        owned = weakref.ref(frame._chunks[0][1])
        frame.close()
        assert owned() is None

    def test_does_not_publish_earlier_chunks_when_later_shading_fails(
        self, session, inputs
    ):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        frame(*inputs)
        second = list(inputs)
        second[4] = lambda n: np.full_like(n, np.nan)
        frame(*second)
        frame.native.payload[0, 1, 3] = 2
        with pytest.raises(GpuError) as failure:
            frame.finish(*inputs[:2])
        assert failure.value.reason is GpuFailure.READBACK_FAILED
        assert not inputs[0].any()
        assert np.isinf(inputs[1]).all()

    @pytest.mark.parametrize(
        "field, expected", [("readback_ms", 3), ("cpu_resolve_ms", 7)]
    )
    def test_reports_distinct_resolution_phases(
        self, session, inputs, monkeypatch, field, expected
    ):
        _, context = session
        frame = GpuFrame(context, 2, 2, 1, 2)
        elapsed = [0]
        read = frame.native.read

        def native_read():
            elapsed[0] += 3_000_000
            return read()

        def shade(normals):
            elapsed[0] += 7_000_000
            return normals

        monkeypatch.setattr(frame.native, "read", native_read)
        monkeypatch.setattr(
            "scripts.wgpu_render_backend.time.perf_counter_ns", lambda: elapsed[0]
        )
        values = list(inputs)
        values[4] = shade
        frame(*values)

        frame.finish(*values[:2])

        assert getattr(frame.stats, field) == expected


class TestNativeFrame:
    @pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5])
    def test_releases_partial_native_allocations(self, monkeypatch, fail_at):
        resources = []
        attempts = 0

        def allocate(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == fail_at:
                raise MemoryError("native_allocation_failed")
            state = SimpleNamespace(destroyed=False)

            def destroy():
                state.destroyed = True

            state.destroy = destroy
            resources.append(state)
            return state

        monkeypatch.setitem(
            sys.modules,
            "wgpu",
            SimpleNamespace(
                TextureUsage=SimpleNamespace(RENDER_ATTACHMENT=1, COPY_SRC=2),
                BufferUsage=SimpleNamespace(
                    VERTEX=1, COPY_DST=2, UNIFORM=4, COPY_SRC=8, MAP_READ=16
                ),
            ),
        )
        owner = SimpleNamespace(
            device=SimpleNamespace(create_texture=allocate, create_buffer=allocate)
        )

        with pytest.raises(MemoryError, match="native_allocation_failed"):
            _Frame(owner, 2, 2, 1, 2)

        assert [resource.destroyed for resource in resources] == [True] * (fail_at - 1)


class TestNativeDevice:
    def test_releases_native_device_wrappers_after_close(self):
        import weakref

        class Device:
            limits = {}

            def __init__(self):
                self.queue = self

            def create_shader_module(self, **kwargs):
                return object()

            def create_render_pipeline(self, **kwargs):
                return SimpleNamespace()

            def destroy(self):
                return None

        device = Device()
        reference = weakref.ref(device)
        owner = _Device(device, {"adapter_type": "IntegratedGPU"})
        del device

        owner.close()

        assert reference() is None


class CandidateAdapter:
    def __init__(self, name, kind="DiscreteGPU", color_bytes=16, attributes=2):
        self.info = {"adapter_type": kind, "device": name}
        self.limits = {
            "max-color-attachment-bytes-per-sample": color_bytes,
            "max-vertex-attributes": attributes,
        }

    def request_device_sync(self, **kwargs):
        return SimpleNamespace(
            limits=self.limits,
            create_shader_module=lambda **kwargs: object(),
            create_render_pipeline=lambda **kwargs: object(),
            destroy=lambda: None,
        )


@pytest.fixture
def adapters(monkeypatch):
    candidates = []
    monkeypatch.setitem(
        sys.modules,
        "wgpu",
        SimpleNamespace(
            gpu=SimpleNamespace(
                enumerate_adapters_sync=lambda: candidates,
                request_adapter_sync=lambda **kwargs: (
                    candidates[0] if candidates else None
                ),
            )
        ),
    )
    return candidates


class TestNativeSelection:
    def test_uses_eligible_device_before_unavailable_later_backend(self, adapters):
        class UnavailableBackend:
            @property
            def info(self):
                raise RuntimeError("unavailable_later_backend")

        adapters.extend([CandidateAdapter("ready"), UnavailableBackend()])
        owner = _native_device(None, False)
        try:
            assert owner.info["device"] == "ready"
        finally:
            owner.close()

    @pytest.mark.parametrize(
        ("kind", "color_bytes", "attributes"),
        [("CPU", 16, 2), ("DiscreteGPU", 8, 2), ("IntegratedGPU", 16, 1)],
        ids=["software", "attachment-limit", "vertex-limit"],
    )
    def test_selects_first_compatible_hardware(
        self, adapters, kind, color_bytes, attributes
    ):
        adapters.extend(
            [
                CandidateAdapter("ineligible", kind, color_bytes, attributes),
                CandidateAdapter("compatible"),
            ]
        )
        owner = _native_device(None, False)
        try:
            assert owner.info["device"] == "compatible"
        finally:
            owner.close()

    def test_preserves_explicit_selection(self, adapters):
        adapters.extend([CandidateAdapter("first"), CandidateAdapter("selected")])
        owner = _native_device("selected", False)
        try:
            assert owner.info["device"] == "selected"
        finally:
            owner.close()

    @pytest.mark.parametrize(
        "names",
        [[], ["other"], ["selected", "selected"]],
        ids=["empty", "missing", "ambiguous"],
    )
    def test_refuses_nonunique_explicit_selection(self, adapters, names):
        adapters.extend(CandidateAdapter(name) for name in names)
        with pytest.raises(GpuError) as failure:
            _native_device("selected", False)
        assert failure.value.reason is GpuFailure.CAPABILITY_UNAVAILABLE

    def test_refuses_absent_compatible_hardware(self, adapters):
        adapters.append(CandidateAdapter("software", "CPU"))
        with pytest.raises(GpuError) as failure:
            _native_device(None, False)
        assert failure.value.reason is GpuFailure.CAPABILITY_UNAVAILABLE

    def test_labels_opted_in_software_execution(self, adapters):
        adapters.append(CandidateAdapter("software", "CPU"))
        owner = _native_device(None, True)
        try:
            assert owner.info["physical_acceleration"] is False
        finally:
            owner.close()

    def test_uses_capability_fallback_after_preference_failure(
        self, adapters, monkeypatch
    ):
        def unavailable(**kwargs):
            raise RuntimeError("preferred_backend_unavailable")

        adapters.append(CandidateAdapter("fallback"))
        monkeypatch.setattr(
            sys.modules["wgpu"].gpu, "request_adapter_sync", unavailable
        )
        owner = _native_device(None, False)
        try:
            assert owner.info["device"] == "fallback"
        finally:
            owner.close()

    def test_retains_preference_failure_when_no_adapter_is_eligible(
        self, adapters, monkeypatch
    ):
        def unavailable(**kwargs):
            raise RuntimeError("preferred_backend_unavailable")

        monkeypatch.setattr(
            sys.modules["wgpu"].gpu, "request_adapter_sync", unavailable
        )
        with pytest.raises(GpuError) as failure:
            _native_device(None, False)
        assert str(failure.value.__cause__) == "preferred_backend_unavailable"

    def test_uses_platform_preference_before_enumeration(self, adapters, monkeypatch):
        preferred = CandidateAdapter("platform-preferred")
        adapters.append(CandidateAdapter("enumerated-first"))
        monkeypatch.setattr(
            sys.modules["wgpu"].gpu, "request_adapter_sync", lambda **kwargs: preferred
        )
        owner = _native_device(None, False)
        try:
            assert owner.info["device"] == "platform-preferred"
        finally:
            owner.close()

    def test_keeps_explicit_selection_independent_of_platform_preference(
        self, adapters, monkeypatch
    ):
        def unavailable(**kwargs):
            raise RuntimeError("preferred_backend_unavailable")

        adapters.append(CandidateAdapter("selected"))
        monkeypatch.setattr(
            sys.modules["wgpu"].gpu, "request_adapter_sync", unavailable
        )
        owner = _native_device("selected", False)
        try:
            assert owner.info["device"] == "selected"
        finally:
            owner.close()

    def test_stops_fallback_before_unavailable_later_backend(self, adapters):
        class UnavailableBackend:
            @property
            def info(self):
                raise RuntimeError("unavailable_later_backend")

        adapters.extend(
            [
                CandidateAdapter("software", "CPU"),
                CandidateAdapter("compatible"),
                UnavailableBackend(),
            ]
        )
        owner = _native_device(None, False)
        try:
            assert owner.info["device"] == "compatible"
        finally:
            owner.close()
