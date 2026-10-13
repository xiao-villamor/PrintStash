"""Portable ONNX provider selection owned exclusively by the compute broker."""

from pathlib import Path

from printstash_core.inference import EmbeddingError

from app.runtime.compute.contracts import ComputeUnavailable, DeviceInfo, Reason


class SessionFactory:
    def __init__(self, device: DeviceInfo):
        try:
            import onnxruntime as ort
            import onnxruntime_ep_webgpu as ep
        except ImportError:
            raise ComputeUnavailable(Reason.RUNTIME_MISSING) from None
        self.ort = ort
        ort.register_execution_provider_library(
            "printstash_webgpu", ep.get_library_path()
        )
        self.devices = [
            candidate
            for candidate in ort.get_ep_devices()
            if candidate.ep_name == ep.get_ep_name()
            and candidate.device.type == ort.OrtHardwareDeviceType.GPU
            and candidate.device.vendor_id == device.vendor_id
            and candidate.device.device_id == device.device_id
        ]
        # Never silently select a different physical adapter in the second runtime.
        if len(self.devices) != 1:
            raise ComputeUnavailable(Reason.ADAPTER_MISSING)

    def __call__(self, payload: bytes, template):
        # Each tower gets its own provider registration; SessionOptions passed
        # by the CPU adapter may be shared across image/text/point construction.
        options = self.ort.SessionOptions()
        options.intra_op_num_threads = template.intra_op_num_threads
        options.inter_op_num_threads = template.inter_op_num_threads
        options.execution_mode = template.execution_mode
        options.enable_cpu_mem_arena = template.enable_cpu_mem_arena
        options.enable_mem_pattern = template.enable_mem_pattern
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        # Refuse a session silently placing its entire graph on CPU. Unsupported
        # graphs return to the existing CPU adapter with its original deadline.
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        options.add_provider_for_devices(
            self.devices,
            {
                "enableGraphCapture": "0",
                "storageBufferCacheMode": "disabled",
                "uniformBufferCacheMode": "disabled",
                "defaultBufferCacheMode": "disabled",
            },
        )
        session = self.ort.InferenceSession(payload, sess_options=options)
        session.disable_fallback()
        return session

    def probe(self) -> None:
        """Real device math without loading a user model or enabling AI features."""
        import numpy as np
        from onnx import TensorProto, helper

        graph = helper.make_graph(
            [helper.make_node("MatMul", ["a", "b"], ["c"])],
            "printstash-device-canary",
            [
                helper.make_tensor_value_info("a", TensorProto.FLOAT, [2, 2]),
                helper.make_tensor_value_info("b", TensorProto.FLOAT, [2, 2]),
            ],
            [helper.make_tensor_value_info("c", TensorProto.FLOAT, [2, 2])],
        )
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
        model.ir_version = 8
        options = self.ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        session = self(model.SerializeToString(), options)
        a = np.asarray([[1, 2], [3, 4]], dtype=np.float32)
        actual = session.run(["c"], {"a": a, "b": a})[0]
        if not isinstance(actual, np.ndarray) or not np.array_equal(actual, a @ a):
            raise ComputeUnavailable(Reason.DEVICE_FAILED)


def native_worker(
    directory: Path, model_key: str, threads: int, factory: SessionFactory
):
    from app.modules.inference.manifest import SparseModelManifest, read_manifest
    from app.modules.inference.onnx_cpu import OnnxCpuProvider
    from app.modules.inference.sparse import SparseNativeProvider
    from app.modules.inference.worker import NativeWorker

    worker = NativeWorker(directory, model_key, threads)
    manifest = read_manifest(directory, model_key)
    try:
        if isinstance(manifest, SparseModelManifest):
            worker._sparse_provider = SparseNativeProvider(
                directory, manifest, threads, session_factory=factory
            )
        else:
            worker._dense_provider = OnnxCpuProvider(
                directory, manifest, threads, session_factory=factory
            )
    except EmbeddingError as exc:
        # Signatures/digests are validated independently by the CPU owner too.
        # A GPU canary mismatch disqualifies this GPU execution, not the Artifact.
        if exc.code == "embedding_canary_mismatch":
            raise ComputeUnavailable(Reason.UNQUALIFIED) from exc
        raise
    return worker
