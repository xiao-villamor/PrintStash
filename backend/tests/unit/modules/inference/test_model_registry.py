"""The curated catalog pins each export: weights, tokenizer, pooling, limits and the guided default."""

import pytest
from pydantic import ValidationError

from app.modules.inference.manifest import SparseModelManifest
from app.modules.inference.model_registry import entries, require


class TestModelRegistry:
    def test_pins_the_small_english_text_export(self):
        entry = require("all-minilm-l6-v2")
        assert entry.repository == "sentence-transformers/all-MiniLM-L6-v2"
        assert entry.revision == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
        assert entry.license == "Apache-2.0"
        assert entry.languages == ("en",)
        assert [(asset.source, asset.size, asset.sha256) for asset in entry.files] == [
            (
                "onnx/model.onnx",
                90405214,
                "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
            ),
            (
                "tokenizer.json",
                466247,
                "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
            ),
        ]

    def test_embeds_the_small_english_model_as_sentence_transformers_does(self):
        text = require("all-minilm-l6-v2").manifest.text
        assert (text.pooling, text.max_tokens, text.pad_id, text.pad_token) == (
            "mean",
            256,
            0,
            "[PAD]",
        )

    def test_uses_no_prefixes_for_the_small_english_model(self):
        manifest = require("all-minilm-l6-v2").manifest
        assert (manifest.query_prefix, manifest.document_prefix) == ("", "")

    def test_pins_the_small_multilingual_text_export(self):
        entry = require("multilingual-e5-small")
        assert entry.repository == "intfloat/multilingual-e5-small"
        assert entry.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
        assert entry.license == "MIT"
        assert [(asset.source, asset.size, asset.sha256) for asset in entry.files] == [
            (
                "onnx/model.onnx",
                470268510,
                "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
            ),
            (
                "tokenizer.json",
                17082730,
                "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
            ),
        ]

    def test_covers_spanish_with_the_multilingual_model(self):
        languages = require("multilingual-e5-small").languages
        assert {"en", "es", "fr", "de", "zh"} <= set(languages)

    def test_embeds_the_multilingual_model_as_sentence_transformers_does(self):
        text = require("multilingual-e5-small").manifest.text
        assert (text.pooling, text.max_tokens, text.pad_id, text.pad_token) == (
            "mean",
            512,
            1,
            "<pad>",
        )

    def test_prefixes_each_multilingual_input_kind(self):
        manifest = require("multilingual-e5-small").manifest
        assert (manifest.query_prefix, manifest.document_prefix) == (
            "query: ",
            "passage: ",
        )

    def test_recommends_one_text_model_for_guided_setup(self):
        assert [
            entry.manifest.model_key for entry in entries() if entry.recommended
        ] == ["bge-small-en-v1.5"]

    def test_pins_the_sparse_document_export(self):
        entry = require("splade-pp-en-v1")
        assert entry.repository == "prithivida/Splade_PP_en_v1"
        assert entry.revision == "762be6a7206e2f299182705972a65e5c46e62be2"
        assert entry.license == "Apache-2.0"
        assert entry.languages == ("en",)
        assert entry.manifest.max_terms == 64
        assert entry.manifest.max_tokens == 512
        assert entry.manifest.vocabulary_size == 30522
        assert (
            entry.id
            == "61075e44331ef757c2e84e3db7c3b1752a1c104c8350ff57982b49fe23322c80"
        )
        assert (
            entry.files[0].sha256
            == "0934583a27a031a66b2e847cbc260fbbef29689e969f500436460ef5146a43f2"
        )
        assert (
            entry.files[1].sha256
            == "2fc687b11de0bc1b3d8348f92e3b49ef1089a621506c7661fbf3248fcd54947e"
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_terms", 0),
            ("max_terms", 129),
            ("max_tokens", 513),
            ("vocabulary_size", 65537),
            ("model_revision", "main"),
            ("pooling", "mean"),
            ("canary", []),
            ("download_url", "https://untrusted.test"),
        ],
    )
    def test_rejects_invalid_sparse_contracts(self, field, value):
        data = require("splade-pp-en-v1").manifest.model_dump()
        data[field] = value
        with pytest.raises(ValidationError):
            SparseModelManifest.model_validate(data)
