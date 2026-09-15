"""Sparse native contracts preserve bounded continuous weights in containment."""

import json
import math

import pytest
from printstash_core.inference import EmbeddingError

from app.db.session import get_session_factory
from app.modules.inference.manifest import read_manifest
from app.modules.inference.sparse import LocalSparseProvider, SparseNativeProvider
from tests.factories.embeddings import sparse_embedding_assets
from tests.paths import BACKEND_DIR


@pytest.fixture
def sparse_provider(db_session, tmp_path):
    directory = sparse_embedding_assets(tmp_path / "sparse")
    return LocalSparseProvider(get_session_factory(), directory, "sparse-contract", 1)


class TestSparse:
    @pytest.mark.parametrize("threads", [0, True, 5])
    def test_rejects_invalid_worker_budgets(self, sparse_provider, threads):
        with pytest.raises(EmbeddingError, match="embedding_thread_budget_invalid"):
            LocalSparseProvider(
                get_session_factory(),
                sparse_provider.directory,
                "sparse-contract",
                threads,
            )

    def test_preserves_continuous_expansion_weights(self, sparse_provider):
        result = sparse_provider.expand("bicycle bracket")
        weights = {term.term: term.weight for term in result.terms}
        assert weights == pytest.approx(
            {
                "bicycle": math.log(4),
                "bike": math.log(3),
                "bracket": math.log(4),
                "mount": math.log(3),
            }
        )
        assert not result.truncated

    def test_reports_sparse_token_truncation(self, sparse_provider):
        result = sparse_provider.expand("bicycle " * 8 + "lamp")
        assert result.truncated
        assert {term.term for term in result.terms} == {"bicycle", "bike"}

    def test_accepts_empty_sparse_outputs(self, sparse_provider):
        assert sparse_provider.expand("unknown").terms == []

    def test_validates_sparse_canaries_without_a_document(self, sparse_provider):
        assert sparse_provider.validate() == sparse_provider.manifest

    @pytest.mark.parametrize("text", ["", "x" * 16385])
    def test_rejects_sparse_input_outside_budget(self, sparse_provider, text):
        with pytest.raises(EmbeddingError, match="embedding_input_budget"):
            sparse_provider.expand(text)

    @pytest.mark.parametrize(
        "change,code",
        [
            ("canary", "canary_mismatch"),
            ("vocabulary_size", "sparse_vocabulary_mismatch"),
            ("opset", "opset_mismatch"),
            ("output_name", "signature_mismatch"),
        ],
    )
    def test_rejects_incompatible_sparse_exports(self, sparse_provider, change, code):
        path = sparse_provider.directory / "manifest.json"
        manifest = json.loads(path.read_text())
        if change == "canary":
            manifest["canary"][0]["weight"] = 0.1
        elif change == "output_name":
            manifest[change] = "wrong"
        else:
            manifest[change] += 1
        path.write_text(json.dumps(manifest))
        contract = read_manifest(sparse_provider.directory, "sparse-contract")
        with pytest.raises(EmbeddingError, match="embedding_" + code):
            SparseNativeProvider(sparse_provider.directory, contract, 1)

    def test_refuses_dense_generation_identity(self, sparse_provider):
        with pytest.raises(EmbeddingError, match="embedding_sparse_not_dense"):
            sparse_provider.manifest.space()

    @pytest.mark.parametrize(
        "fault",
        ["identity", "duplicate", "too_many", "nonfinite", "negative", "unknown_field"],
    )
    def test_rejects_malformed_sparse_pipe_results(
        self, sparse_provider, monkeypatch, fault
    ):
        import subprocess
        import sys

        from app.modules.inference.manifest import manifest_identity

        payload = {
            "config_hash": manifest_identity(sparse_provider.manifest),
            "terms": [{"term": "bike", "weight": 1.0}],
            "truncated": False,
        }
        if fault == "identity":
            payload["config_hash"] = "f" * 64
        elif fault == "duplicate":
            payload["terms"] *= 2
        elif fault == "too_many":
            payload["terms"] = [{"term": f"word{i}", "weight": 1} for i in range(5)]
        elif fault == "unknown_field":
            payload["unexpected"] = True
        else:
            payload["terms"][0]["weight"] = float("nan") if fault == "nonfinite" else -1
        monkeypatch.setattr(
            sparse_provider,
            "_spawn",
            lambda: subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.fakes.sparse_worker",
                    json.dumps(payload),
                ],
                cwd=BACKEND_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            ),
        )
        with pytest.raises(EmbeddingError, match="embedding_output_(invalid|mismatch)"):
            sparse_provider.expand("bicycle")

    def test_contains_a_stalled_sparse_worker(self, sparse_provider, monkeypatch):
        import subprocess
        import sys

        from printstash_core.inference import InferenceContext

        monkeypatch.setattr(
            sparse_provider,
            "_spawn",
            lambda: subprocess.Popen(
                [sys.executable, "-m", "tests.fakes.sparse_worker", "stall"],
                cwd=BACKEND_DIR,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            ),
        )
        with pytest.raises(EmbeddingError, match="inference_timeout"):
            sparse_provider.expand("bicycle", context=InferenceContext.bounded(0.1))

    def test_contains_sparse_native_memory(self, sparse_provider, monkeypatch):
        from app.modules.media import compute_slots

        monkeypatch.setattr(compute_slots, "native_memory_budget_bytes", lambda: 1)
        with pytest.raises(EmbeddingError, match="embedding_worker_oom"):
            sparse_provider.validate()
