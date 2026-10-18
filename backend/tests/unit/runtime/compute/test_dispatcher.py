"""Independent native startup failures do not disable another compute runtime."""

import pytest

from app.modules.inference import webgpu
from app.runtime.compute import dispatcher
from app.runtime.compute.contracts import ComputeMode, DeviceInfo, Operation, Reason


class Device:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class NativeProviderError(Exception):
    pass


class TestRuntimeIsolation:
    def test_keeps_render_device_when_inference_runtime_fails(
        self, tmp_path, monkeypatch
    ):
        device = Device()
        info = DeviceInfo(
            identity="physical-test-device",
            name="contract",
            vendor_id=1,
            device_id=2,
            driver="contract",
            backend="Vulkan",
        )
        monkeypatch.setattr(dispatcher, "discover", lambda selector: (info, device))

        def unavailable(info):
            raise NativeProviderError()

        monkeypatch.setattr(webgpu, "SessionFactory", unavailable)

        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        try:
            owner.start_inference()
            status = owner.status()
            reasons = {
                capability.operation: capability.reason
                for capability in status.capabilities
            }
            assert owner.device is device
            assert not device.destroyed
            assert reasons[Operation.RENDER] is Reason.UNQUALIFIED
            assert reasons[Operation.DENSE] is Reason.DEVICE_FAILED
        finally:
            owner.close()
        assert device.destroyed


class TestRenderStartup:
    def test_preview_does_not_start_inference(self, tmp_path, monkeypatch):
        from app.core.config import _overlay

        _overlay["compute_render_policy"] = "preview"
        device = Device()
        info = DeviceInfo(
            identity="physical",
            name="hardware",
            vendor_id=1,
            device_id=2,
            driver="test",
            backend="OpenGL",
        )
        monkeypatch.setattr(dispatcher, "discover", lambda selector: (info, device))

        def forbidden(info):
            pytest.fail("render startup initialized inference")

        import pytest

        monkeypatch.setattr(webgpu, "SessionFactory", forbidden)
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        try:
            caps = {c.operation: c for c in owner.status().capabilities}
            assert caps[Operation.RENDER].available
            assert caps[Operation.RENDER].reason is Reason.PREVIEW
            assert not caps[Operation.DENSE].available
            assert owner.factory is None
        finally:
            owner.close()


class TestRenderTelemetry:
    def test_counts_frames_separately_from_native_submissions(
        self, tmp_path, monkeypatch
    ):
        import sys

        import numpy as np
        from printstash_core.mesh.render_geometry import PreparedRender

        from app.modules.media.webgpu_render import Renderer
        from tests.fakes.gpu_device import API
        from tests.fakes.gpu_device import Device as GpuDevice

        monkeypatch.setitem(sys.modules, "wgpu", API)
        device = GpuDevice()
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
        )
        renderer = Renderer(device, owner.memory)
        owner.renderer = renderer
        faces = np.asarray([[0, 1, 2]], dtype=np.int64)
        prepared = PreparedRender(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            1,
            lambda size: iter([faces]),
            np.arange(3, dtype=np.int64),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        try:
            renderer.execute_prepared_many(
                [
                    (prepared, 24, 24, [None, np.eye(3)], False),
                    (prepared, 24, 24, [None], False),
                ],
                ["geometry:a", "geometry:b"],
                ["a", "b"],
                raw=True,
            )
            status = owner.status()
            assert status.render_frames == 3
            assert status.render_submissions == status.render_batches == 1
            assert status.render_upload_calls == device.mapped_writes == 1
            assert status.render_bounds_vertices == 6
            assert status.geometry_packing_min_faces is None
            assert status.geometry_local_normal_fallback_vertices == 0
            assert status.geometry_cpu_normal_fallbacks == 0
        finally:
            renderer.close()
            owner.close()


@pytest.fixture
def dispatcher_owner(tmp_path, monkeypatch):
    from tests.fakes.gpu_device import Device as NativeDevice

    owner = dispatcher.Dispatcher(
        tmp_path / "compute", mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
    )
    owner.root.mkdir()
    owner.device = NativeDevice()
    owner.host_capacity = 1024**3
    monkeypatch.setattr(owner, "admit_host", lambda deadline: None)
    yield owner
    owner.close()


class TestNativeInferenceResidency:
    @pytest.mark.parametrize("failed_load", [False, True])
    def test_preserves_worker_residency_custody(
        self, dispatcher_owner, tmp_path, monkeypatch, failed_load
    ):
        import base64
        import json
        import time
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.modules.inference import manifest
        from app.runtime.compute.protocol import InferenceRequest, Priority
        from tests.factories.embeddings import local_embedding_assets

        owner = dispatcher_owner
        directory = local_embedding_assets(tmp_path / "assets")
        contract = manifest.read_manifest(directory, "two-tower-contract")
        native = Mock(manifest=contract)
        native.execute.return_value = json.dumps(
            {
                "config_hash": contract.space().config_hash,
                "vectors": [[1.0, 0.0, 0.0]],
                "truncated": [False],
            }
        ).encode()
        loader = (
            Mock(side_effect=ValueError("provider failed"))
            if failed_load
            else Mock(return_value=native)
        )
        monkeypatch.setattr(webgpu, "native_worker", loader)
        owner.factory = Mock()
        peaks = SimpleNamespace(peak_host_bytes=1024, peak_device_bytes=1024)
        monkeypatch.setattr(owner, "qualification", lambda *args: peaks)
        message = {
            "config_hash": contract.space().config_hash,
            "inputs": [{"modality": "text", "text": "red"}],
        }
        request = InferenceRequest(
            deadline=time.monotonic() + 20,
            priority=Priority.INTERACTIVE,
            request_id="unit",
            directory=str(directory),
            model_key=contract.model_key,
            threads=1,
            payload=base64.b64encode(json.dumps(message).encode()).decode(),
        )
        if failed_load:
            with pytest.raises(ValueError, match="provider failed"):
                owner.execute(request)
            assert owner.workers == owner.host_sizes == owner.memory.entries == {}
            assert owner.active_deadline is None
            return
        assert owner.execute(request) == native.execute.return_value
        peaks.peak_host_bytes = peaks.peak_device_bytes = 2048
        assert owner.execute(request) == native.execute.return_value
        assert loader.call_count == 1
        assert (
            owner.residency_misses,
            owner.residency_hits,
            owner.completed,
            owner.batch_inputs,
        ) == (1, 1, 2, 2)
        assert next(iter(owner.memory.entries.values())).size == 2048
        assert all(entry.pins == 0 for entry in owner.memory.entries.values())
        assert owner.status().resident_model_ids == [
            manifest.manifest_identity(contract)
        ]
        owner.memory.evict_idle()
        assert owner.workers == owner.host_sizes == {}

    def test_refuses_inference_without_native_runtime(self, dispatcher_owner):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        with pytest.raises(ComputeUnavailable) as exc:
            dispatcher_owner.inference(None)
        assert exc.value.reason is Reason.RUNTIME_MISSING


class TestHostAdmission:
    def test_releases_the_shared_host_claim_on_close(self, tmp_path):
        import time

        owner = dispatcher.Dispatcher(
            tmp_path / "compute",
            mode=ComputeMode.CPU,
            selector=None,
            budget_bytes=1024**3,
        )
        owner.admit_host(time.monotonic() + 2)
        claim = owner.host_claim
        assert claim is not None
        owner.admit_host(time.monotonic() + 2)
        assert owner.host_claim is claim
        assert not owner.pressure()
        owner.close()
        assert owner.host_claim is None

    def test_refuses_expired_host_lock_wait(self, tmp_path):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
        )
        owner.host_lock.acquire()
        try:
            with pytest.raises(ComputeUnavailable) as exc:
                owner.admit_host(0)
            assert exc.value.reason is Reason.DEADLINE
        finally:
            owner.host_lock.release()

    def test_evicts_idle_host_residency_before_refusal(self, dispatcher_owner):
        from app.runtime.compute import geometry_cache
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        owner = dispatcher_owner
        owner.host_capacity = (
            192 * 1024**2
            + geometry_cache.INPUT_CACHE_BYTES
            + geometry_cache.INPUT_QUEUE_BYTES
            + geometry_cache.OUTPUT_QUEUE_BYTES
            + 100
        )
        owner.host_sizes["old"] = 80
        owner.memory.reserve("old", 1, lambda: owner.host_sizes.pop("old"), 0)
        owner.make_host_room("new", 80)
        assert owner.host_sizes == {}
        with pytest.raises(ComputeUnavailable) as exc:
            owner.make_host_room("new", 101)
        assert exc.value.reason is Reason.CAPACITY


class TestRenderDispatch:
    @pytest.mark.parametrize("binary", [False, True])
    @pytest.mark.parametrize("allocations", [0, 1, 2])
    def test_retries_only_one_native_allocation_failure(
        self, dispatcher_owner, monkeypatch, binary, allocations
    ):
        import base64
        import sys
        import time
        from unittest.mock import Mock

        import numpy as np
        from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
        from printstash_core.mesh.render_geometry import PreparedRender

        from app.core.config import _overlay
        from app.modules.media.compute_geometry import decode, encode, split
        from app.modules.media.compute_render import RECIPE
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.protocol import (
            BinaryRenderRequest,
            Priority,
            RenderRequest,
        )
        from tests.fakes.gpu_device import API

        monkeypatch.setitem(sys.modules, "wgpu", API)
        monkeypatch.setitem(_overlay, "compute_render_policy", "preview")
        owner = dispatcher_owner
        prepared = PreparedRender(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            1,
            lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
            np.arange(3),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        payload = encode(prepared, 24, 24, [None], False)
        common = dict(
            deadline=time.monotonic() + 10,
            priority=Priority.BACKGROUND,
            request_id="render",
            recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:24x24:1:0",
            units=1,
        )
        if binary:
            key, header, body = split(payload)
            request = BinaryRenderRequest(
                **common, geometry_key=key, geometry_bytes=len(body), header=header
            )
            request._decoded = decode(payload)
        else:
            request = RenderRequest(
                **common, payload=base64.b64encode(payload).decode()
            )
        # Inject only the optional native renderer; admission, budgets and receipts remain real.
        from app.modules.media import webgpu_render

        renderer = webgpu_render.Renderer(owner.device, owner.memory)
        method = Mock(side_effect=[MemoryError()] * allocations + [[b"pixels"]])
        monkeypatch.setattr(
            renderer, "execute_prepared_many" if binary else "execute_many", method
        )
        monkeypatch.setattr(webgpu_render, "Renderer", lambda *args: renderer)
        if allocations == 2:
            with pytest.raises(ComputeUnavailable) as exc:
                owner.execute_render_batch([request])
            assert exc.value.reason is Reason.CAPACITY
        else:
            assert owner.execute_render_batch([request]) == (
                [b"\x00pixels"] if binary else [b"pixels"]
            )
            assert owner.completed == 1
            assert owner.output_bytes == len(b"\x00pixels" if binary else b"pixels")
        assert method.call_count == min(allocations + 1, 2)
        assert owner.memory.entries["renderer"].pins == 0
        assert owner.active_deadline is None


class TestDispatcherEligibility:
    @pytest.mark.parametrize("outcome", ["healthy", "unavailable", "native"])
    def test_tracks_independent_inference_startup(
        self, dispatcher_owner, monkeypatch, outcome
    ):
        from unittest.mock import Mock

        from app.runtime.compute.contracts import ComputeUnavailable, DeviceInfo, Reason

        owner = dispatcher_owner
        owner.device_info = DeviceInfo(
            identity="gpu",
            name="gpu",
            vendor_id=1,
            device_id=2,
            driver="test",
            backend="Vulkan",
        )
        factory = Mock()
        if outcome != "healthy":
            factory.probe.side_effect = (
                ComputeUnavailable(Reason.RUNTIME_MISSING)
                if outcome == "unavailable"
                else RuntimeError("plugin failed")
            )
        constructor = Mock(return_value=factory)
        monkeypatch.setattr(webgpu, "SessionFactory", constructor)
        owner.start_inference()
        assert (
            owner.inference_reason
            is {
                "healthy": Reason.UNQUALIFIED,
                "unavailable": Reason.RUNTIME_MISSING,
                "native": Reason.DEVICE_FAILED,
            }[outcome]
        )
        assert (owner.factory is factory) is (outcome == "healthy")
        if outcome == "healthy":
            owner.start_inference()
            assert constructor.call_count == 1

    def test_reports_failed_device_discovery(self, tmp_path, monkeypatch):
        from unittest.mock import Mock

        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        monkeypatch.setattr(
            dispatcher,
            "discover",
            Mock(side_effect=ComputeUnavailable(Reason.SOFTWARE_ADAPTER)),
        )
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        assert owner.reason is Reason.SOFTWARE_ADAPTER
        assert owner.device is None
        owner.close()

    def test_requires_matching_qualification_receipt(self, dispatcher_owner):
        import json

        from app.runtime.compute.contracts import ComputeUnavailable, DeviceInfo, Reason
        from app.runtime.compute.qualification import Qualification

        owner = dispatcher_owner
        owner.device_info = DeviceInfo(
            identity="gpu",
            name="gpu",
            vendor_id=1,
            device_id=2,
            driver="test",
            backend="Vulkan",
        )
        receipt = Qualification(
            device_identity="gpu",
            runtime_identity=owner.runtime,
            operation=Operation.RENDER,
            recipe="tested",
            minimum_units=1,
            maximum_units=8,
            quality_passed=True,
            source_to_publication_speedup=2,
            interactive_p95_ratio=1,
            peak_device_bytes=1,
            peak_host_bytes=1,
        )
        (owner.root / "qualification.json").write_text(
            json.dumps([receipt.model_dump(mode="json")])
        )
        assert owner.qualification(Operation.RENDER, "tested", 4) == receipt
        with pytest.raises(ComputeUnavailable) as exc:
            owner.qualification(Operation.RENDER, "other", 4)
        assert exc.value.reason is Reason.UNQUALIFIED
        owner.device = None
        with pytest.raises(ComputeUnavailable) as exc:
            owner.qualification(Operation.RENDER, "tested", 4)
        assert exc.value.reason is Reason.DISABLED

    @pytest.mark.parametrize("batch", [False, True])
    def test_refuses_expired_work_before_dispatch(self, dispatcher_owner, batch):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.protocol import Priority, RenderRequest

        request = RenderRequest(
            deadline=1,
            priority=Priority.INTERACTIVE,
            request_id="expired",
            recipe="test",
            units=1,
            payload="",
        )
        with pytest.raises(ComputeUnavailable) as exc:
            dispatcher_owner.execute_render_batch(
                [request]
            ) if batch else dispatcher_owner.execute(request)
        assert exc.value.reason is Reason.DEADLINE
        assert dispatcher_owner.completed == 0

    def test_rejects_an_unadmitted_binary_geometry(self, dispatcher_owner):
        import time

        from app.runtime.compute.protocol import (
            BinaryRenderRequest,
            Priority,
            RenderRequest,
        )

        request = BinaryRenderRequest(
            deadline=time.monotonic() + 10,
            priority=Priority.INTERACTIVE,
            request_id="unadmitted",
            recipe="test",
            units=1,
            geometry_key="a" * 64,
            geometry_bytes=1,
            header="{}",
        )
        with pytest.raises(ValueError, match="compute_geometry_not_admitted"):
            dispatcher_owner.render_many([request])
        legacy = RenderRequest(
            deadline=request.deadline,
            priority=request.priority,
            request_id="legacy",
            recipe="test",
            units=1,
            payload="",
        )
        with pytest.raises(ValueError, match="compute_batch_protocol"):
            dispatcher_owner.render_many([request, legacy])


class TestReplyDeadlines:
    def test_withholds_a_reply_completed_after_its_deadline(
        self, dispatcher_owner, monkeypatch
    ):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.protocol import Priority, RenderRequest

        clock = [10.0]
        monkeypatch.setattr(dispatcher.time, "monotonic", lambda: clock[0])

        def render(request):
            clock[0] = 12.0
            return b"late"

        monkeypatch.setattr(dispatcher_owner, "render", render)
        request = RenderRequest(
            deadline=11.0,
            priority=Priority.INTERACTIVE,
            request_id="late",
            recipe="test",
            units=1,
            payload="",
        )
        with pytest.raises(ComputeUnavailable) as exc:
            dispatcher_owner.execute(request)
        assert exc.value.reason is Reason.DEADLINE
        assert dispatcher_owner.completed == dispatcher_owner.output_bytes == 0
        assert dispatcher_owner.active_deadline is None

    def test_refuses_rendering_without_a_device(self, dispatcher_owner):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        dispatcher_owner.device = None
        with pytest.raises(ComputeUnavailable) as exc:
            dispatcher_owner.render_many([])
        assert exc.value.reason is Reason.DISABLED
