"""Tensor batches preserve input ordering, normalization and legacy graph behavior.

Graphs load from the exact file whose digest was checked, by descriptor.
"""

import hashlib
import os
from pathlib import Path

import numpy as np
import psutil
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput

from app.modules.inference.manifest import ModelAsset, read_manifest
from app.modules.inference.onnx_cpu import OnnxCpuProvider, verified_graph
from tests.factories.embeddings import local_embedding_assets, text_embedding_assets


def _graph_asset(directory: Path) -> tuple[Path, ModelAsset]:
    manifest = read_manifest(directory, "two-tower-contract")
    path = directory / manifest.image.graph.filename
    return path, manifest.image.graph


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

    def test_keeps_truncating_after_counting_tokens(self, tmp_path):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=True)
        provider = OnnxCpuProvider(
            directory, read_manifest(directory, "two-tower-contract"), 1
        )
        long = (EmbeddingInput("text", text="red " * 12),)

        first = provider.embed(long, provider.space)
        second = provider.embed(long, provider.space)

        assert (second, provider.truncations) == (first, [True])

    def test_builds_sessions_from_the_verified_file(self, tmp_path):
        directory = local_embedding_assets(tmp_path / "assets", dynamic_batch=True)
        manifest = read_manifest(directory, "two-tower-contract")
        sources = []

        def factory(source, options):
            import onnxruntime as ort

            sources.append((source, Path(source).read_bytes()))
            return ort.InferenceSession(
                source, sess_options=options, providers=["CPUExecutionProvider"]
            )

        OnnxCpuProvider(directory, manifest, 1, session_factory=factory)

        assert [content for _, content in sources] == [
            (directory / manifest.image.graph.filename).read_bytes(),
            (directory / manifest.text.graph.filename).read_bytes(),
        ]
        assert all(isinstance(source, str) for source, _ in sources)


class TestVerifiedGraph:
    def test_yields_the_verified_bytes(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        with verified_graph(path.parent, asset) as source:
            assert Path(source).read_bytes() == path.read_bytes()

    def test_keeps_the_verified_file_after_a_rename_swap(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        original = path.read_bytes()
        with verified_graph(path.parent, asset) as source:
            replacement = path.with_name("replacement.onnx")
            replacement.write_bytes(b"attacker graph")
            os.replace(replacement, path)
            assert Path(source).read_bytes() == original

    def test_refuses_bytes_that_differ_from_the_digest(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        path.write_bytes(b"replaced graph")
        with pytest.raises(EmbeddingError, match="embedding_asset_digest_mismatch"):
            with verified_graph(path.parent, asset):
                pass

    def test_refuses_a_symlinked_graph(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        target = path.with_name("elsewhere.onnx")
        os.replace(path, target)
        path.symlink_to(target)
        with pytest.raises(EmbeddingError, match="embedding_asset_invalid"):
            with verified_graph(path.parent, asset):
                pass

    def test_refuses_a_graph_outside_its_declared_opset(self, tmp_path):
        directory = text_embedding_assets(tmp_path / "text")
        manifest = read_manifest(directory, "text-contract")
        with pytest.raises(EmbeddingError, match="embedding_opset_mismatch"):
            with verified_graph(
                directory, manifest.text.graph, opset=manifest.text.opset + 1
            ):
                pass

    def test_releases_its_descriptor(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        before = psutil.Process().num_fds()
        with verified_graph(path.parent, asset):
            pass
        assert psutil.Process().num_fds() == before

    def test_identifies_the_asset_it_verified(self, tmp_path):
        path, asset = _graph_asset(local_embedding_assets(tmp_path / "assets"))
        with verified_graph(path.parent, asset) as source:
            assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == asset.sha256
