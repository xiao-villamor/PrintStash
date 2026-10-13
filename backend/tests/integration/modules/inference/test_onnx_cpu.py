"""Tensor batches preserve input ordering, normalization and legacy graph behavior."""

import numpy as np
import pytest
from printstash_core.inference import EmbeddingInput

from app.modules.inference.manifest import read_manifest
from app.modules.inference.onnx_cpu import OnnxCpuProvider
from tests.factories.embeddings import local_embedding_assets


class TestOnnxCpuProvider:
    @pytest.mark.parametrize("dynamic", [False, True], ids=["fixed", "dynamic"])
    def test_preserves_text_batch_association(self, tmp_path, dynamic):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=dynamic)
        provider = OnnxCpuProvider(
            directory, read_manifest(directory, "two-tower-contract"), 1
        )
        inputs = (
            EmbeddingInput("text", text="red"),
            EmbeddingInput("text", text="blue"),
        )

        result = provider.embed(inputs, provider.space)

        np.testing.assert_allclose(result, [[1, 0, 0], [0, 0, 1]], atol=1e-6)

    def test_preserves_image_batch_association(self, tmp_path):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=True)
        provider = OnnxCpuProvider(
            directory, read_manifest(directory, "two-tower-contract"), 1
        )
        inputs = tuple(
            EmbeddingInput("image", rgb=color, width=1, height=1)
            for color in (bytes([255, 0, 0]), bytes([0, 0, 255]))
        )

        result = provider.embed(inputs, provider.space)

        np.testing.assert_allclose(result, [[1, 0, 0], [0, 0, 1]], atol=1e-6)

    def test_preserves_mixed_modality_order(self, tmp_path):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=True)
        provider = OnnxCpuProvider(
            directory, read_manifest(directory, "two-tower-contract"), 1
        )
        inputs = (
            EmbeddingInput("text", text="blue"),
            EmbeddingInput("image", rgb=bytes([255, 0, 0]), width=1, height=1),
            EmbeddingInput("text", text="red"),
        )

        result = provider.embed(inputs, provider.space)

        np.testing.assert_allclose(result, [[0, 0, 1], [1, 0, 0], [1, 0, 0]], atol=1e-6)

    def test_reports_truncation_per_batch_member(self, tmp_path):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=True)
        provider = OnnxCpuProvider(
            directory, read_manifest(directory, "two-tower-contract"), 1
        )

        provider.embed(
            (
                EmbeddingInput("text", text="red " * 12),
                EmbeddingInput("text", text="blue"),
            ),
            provider.space,
        )

        assert provider.truncations == [True, False]
