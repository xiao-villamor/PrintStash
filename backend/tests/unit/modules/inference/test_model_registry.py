"""The sparse catalog pins weights, tokenizer, document pooling and term limits."""

import pytest
from pydantic import ValidationError

from app.modules.inference.manifest import SparseModelManifest
from app.modules.inference.model_registry import require


class TestModelRegistry:
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
