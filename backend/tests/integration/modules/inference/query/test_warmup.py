"""Cold interactive requests must not repeatedly kill a model before it loads."""

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput

from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.query import QueryRunner
from tests.factories.embeddings import text_embedding_assets


class TestLocalQueryWarmup:
    def test_returns_promptly_while_a_model_is_cold(self, db_session, tmp_path):
        directory = text_embedding_assets(tmp_path)
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        runner = QueryRunner()
        try:
            with pytest.raises(EmbeddingError, match="embedding_model_warming"):
                runner.embed(
                    provider,
                    provider.space,
                    EmbeddingInput("text", text="red"),
                    authorization="user",
                    seconds=0.05,
                )
        finally:
            runner.close()

    def test_preserves_cached_vectors_after_worker_eviction(self, db_session, tmp_path):
        from app.modules.inference.worker_pool import pool

        directory = text_embedding_assets(tmp_path)
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        provider.validate()
        runner = QueryRunner()
        try:
            value = EmbeddingInput("text", text="red")
            first = runner.embed(
                provider, provider.space, value, authorization="user", seconds=1
            )
            pool.close()
            assert (
                runner.embed(
                    provider, provider.space, value, authorization="user", seconds=0.05
                )
                == first
            )
            assert not provider.is_warm
        finally:
            runner.close()

    def test_invalidates_readiness_after_an_asset_changes(self, db_session, tmp_path):
        directory = text_embedding_assets(tmp_path)
        provider = LocalEmbeddingProvider(
            get_session_factory(), directory, "text-contract", 1
        )
        provider.validate()
        assert provider.is_warm
        path = directory / "text.onnx"
        path.write_bytes(path.read_bytes() + b"changed")
        assert not provider.is_warm
        with pytest.raises(EmbeddingError, match="embedding_model_warming"):
            provider.prepare_query()
