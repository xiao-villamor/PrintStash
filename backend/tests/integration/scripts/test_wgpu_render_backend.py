"""Native-boundary conformance, without claiming physical GPU qualification."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from scripts.render_backend import GpuError, GpuFailure
from scripts.wgpu_render_backend import GpuContext, GpuFrame, is_hardware


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
