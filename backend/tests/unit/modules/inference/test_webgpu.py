"""Optional native provider boundaries preserve physical identity and failures."""

import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError

from app.modules.inference import webgpu
from app.runtime.compute.contracts import ComputeUnavailable, DeviceInfo, Reason


@pytest.fixture
def device_info():
    return DeviceInfo(
        identity="gpu",
        name="adapter",
        vendor_id=7,
        device_id=9,
        driver="test",
        backend="Vulkan",
    )


@pytest.fixture
def native_api(monkeypatch):
    options = Mock()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    ort = NS(
        register_execution_provider_library=Mock(),
        get_ep_devices=Mock(
            return_value=[
                NS(ep_name="webgpu", device=NS(type="GPU", vendor_id=7, device_id=9))
            ]
        ),
        OrtHardwareDeviceType=NS(GPU="GPU"),
        SessionOptions=Mock(return_value=options),
        InferenceSession=Mock(),
    )
    ep = NS(
        get_library_path=lambda: "contract-native-library", get_ep_name=lambda: "webgpu"
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "onnxruntime_ep_webgpu", ep)
    return ort, options


class TestNativeSessions:
    def test_preserves_session_policy_on_selected_device(self, native_api, device_info):
        ort, options = native_api
        owner = webgpu.SessionFactory(device_info)
        template = NS(
            intra_op_num_threads=2,
            inter_op_num_threads=1,
            execution_mode="sequential",
            enable_cpu_mem_arena=False,
            enable_mem_pattern=False,
        )
        session = owner(b"model", template)
        assert session is ort.InferenceSession.return_value
        assert options.intra_op_num_threads == 2
        assert options.enable_cpu_mem_arena is False
        assert ("session.disable_cpu_ep_fallback", "1") in [
            call.args for call in options.add_session_config_entry.call_args_list
        ]
        session.disable_fallback.assert_called_once_with()
        assert options.add_provider_for_devices.call_args.args[0] == owner.devices
        assert (
            options.add_provider_for_devices.call_args.args[1]["storageBufferCacheMode"]
            == "disabled"
        )

    @pytest.mark.parametrize("count", [0, 2])
    def test_refuses_ambiguous_adapter_identity(self, native_api, device_info, count):
        ort, _ = native_api
        ort.get_ep_devices.return_value *= count
        with pytest.raises(ComputeUnavailable) as exc:
            webgpu.SessionFactory(device_info)
        assert exc.value.reason is Reason.ADAPTER_MISSING

    def test_reports_missing_optional_runtime(self, monkeypatch, device_info):
        monkeypatch.setitem(sys.modules, "onnxruntime_ep_webgpu", None)
        with pytest.raises(ComputeUnavailable) as exc:
            webgpu.SessionFactory(device_info)
        assert exc.value.reason is Reason.RUNTIME_MISSING

    @pytest.mark.parametrize("valid", [True, False])
    def test_checks_native_math_result(self, native_api, device_info, valid):
        ort, _ = native_api
        ort.InferenceSession.return_value.run.return_value = [
            np.asarray([[7, 10], [15, 22]], dtype=np.float32) if valid else None
        ]
        owner = webgpu.SessionFactory(device_info)
        if valid:
            assert owner.probe() is None
        else:
            with pytest.raises(ComputeUnavailable) as exc:
                owner.probe()
            assert exc.value.reason is Reason.DEVICE_FAILED


class TestProviderWorkers:
    @pytest.mark.parametrize("sparse", [False, True])
    def test_attaches_native_session_factory(self, tmp_path, monkeypatch, sparse):
        from app.modules.inference import onnx_cpu
        from app.modules.inference import sparse as sparse_module
        from tests.factories.embeddings import (
            local_embedding_assets,
            sparse_embedding_assets,
        )

        directory = (sparse_embedding_assets if sparse else local_embedding_assets)(
            tmp_path / "assets"
        )
        factory = Mock()
        provider = Mock()
        monkeypatch.setattr(
            sparse_module if sparse else onnx_cpu,
            "SparseNativeProvider" if sparse else "OnnxCpuProvider",
            provider,
        )
        owner = webgpu.native_worker(
            directory, "sparse-contract" if sparse else "two-tower-contract", 2, factory
        )
        assert (
            owner._sparse_provider if sparse else owner._dense_provider
        ) is provider.return_value
        assert provider.call_args.kwargs == {"session_factory": factory}

    @pytest.mark.parametrize(
        "code,reason",
        [
            ("embedding_canary_mismatch", Reason.UNQUALIFIED),
            ("embedding_input_invalid", None),
        ],
    )
    def test_preserves_provider_failure_identity(
        self, tmp_path, monkeypatch, code, reason
    ):
        from app.modules.inference import onnx_cpu
        from tests.factories.embeddings import local_embedding_assets

        directory = local_embedding_assets(tmp_path / "assets")
        monkeypatch.setattr(
            onnx_cpu, "OnnxCpuProvider", Mock(side_effect=EmbeddingError(code))
        )
        with pytest.raises(ComputeUnavailable if reason else EmbeddingError) as exc:
            webgpu.native_worker(directory, "two-tower-contract", 1, Mock())
        assert (exc.value.reason if reason else exc.value.code) == (reason or code)
