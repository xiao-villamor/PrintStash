"""Native text contract, pooling, truncation and graph admission."""

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput

from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import read_manifest
from tests.factories.embeddings import text_embedding_assets


class TestLocalText:
    @pytest.mark.parametrize(
        "pooling, expected", [("cls", [1, 0, 0]), ("mean", [2**-0.5, 0, 2**-0.5])]
    )
    def test_embeds_text_with_declared_pooling(
        self, db_session, tmp_path, pooling, expected
    ):
        directory = text_embedding_assets(tmp_path, pooling=pooling)
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        output = provider.embed(
            (EmbeddingInput("text", text="red blue"),), provider.space
        )
        np.testing.assert_allclose(output[0], expected, atol=1e-6)
        assert provider.last_truncations == (False,)

    def test_reports_actual_token_truncation(self, db_session, tmp_path):
        directory = text_embedding_assets(tmp_path)
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        provider.embed(
            (
                EmbeddingInput("text", text="red " * 9),
                EmbeddingInput("text", text="red"),
            ),
            provider.space,
        )
        assert provider.last_truncations == (True, False)

    def test_applies_text_prefixes_only_once(self, db_session, tmp_path):
        directory = text_embedding_assets(tmp_path)
        original = read_manifest(directory, "text-contract").space()
        space = replace(original, query_prefix="blue ")
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1, space=space
        )
        # The search owner already formats both query and document inputs.
        assert provider.embed((EmbeddingInput("text", text="red"),), space) == (
            (1, 0, 0),
        )
        assert provider.embed(
            (EmbeddingInput("text", text=space.query_prefix + "red"),), space
        ) == ((0, 0, 1),)

    @pytest.mark.parametrize(
        "invalid, code",
        [
            ("opset", "opset_mismatch"),
            ("signature", "signature_mismatch"),
            ("external", "external_data_forbidden"),
            ("domain", "opset_mismatch"),
        ],
    )
    def test_rejects_undeclared_onnx_graph_contracts(
        self, db_session, tmp_path, invalid, code
    ):
        import onnx

        directory = text_embedding_assets(tmp_path)
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        if invalid == "opset":
            manifest["text"]["opset"] = 16
        elif invalid == "signature":
            manifest["text"]["input_name"] = "wrong"
        else:
            graph = onnx.load(directory / "text.onnx")
            if invalid == "external":
                tensor = graph.graph.initializer[0]
                tensor.data_location = onnx.TensorProto.EXTERNAL
                tensor.external_data.add(key="location", value="/private/outside.bin")
                tensor.ClearField("raw_data")
            else:
                graph.opset_import.add(domain="unsafe.custom", version=1)
            (directory / "text.onnx").write_bytes(graph.SerializeToString())
            manifest["text"]["graph"]["sha256"] = hashlib.sha256(
                (directory / "text.onnx").read_bytes()
            ).hexdigest()
        path.write_text(json.dumps(manifest))
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        with pytest.raises(EmbeddingError, match=code):
            provider.validate()
