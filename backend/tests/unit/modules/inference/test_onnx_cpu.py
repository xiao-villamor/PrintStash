"""Graph admission inspects all tensor containers before creating a native session."""

import pytest
from printstash_core.inference import EmbeddingError

from app.modules.inference.onnx_cpu import OnnxCpuProvider
from tests.factories.embeddings import onnx_external_tensor_graph


class TestOnnxCpuProvider:
    @pytest.mark.parametrize(
        "container",
        [
            "initializer",
            "sparse_initializer",
            "tensor",
            "tensors",
            "sparse_tensor",
            "sparse_tensors",
            "graph",
            "graphs",
        ],
    )
    def test_rejects_hidden_external_tensor_data(self, container):
        payload = onnx_external_tensor_graph(container)
        with pytest.raises(EmbeddingError, match="embedding_external_data_forbidden"):
            OnnxCpuProvider._verify_text_graph(payload, 17)
