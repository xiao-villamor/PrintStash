"""Local semantic search exposes only current, editable vectors in one native Space."""

import pytest

from app.core.config import _overlay
from app.core.errors import OperationError
from app.db.models import IndexGeneration
from app.db.session import get_session_factory
from app.modules.inference import search, store
from app.modules.inference.local import configured_provider
from app.modules.similarity import configuration
from tests.factories.embeddings import local_embedding_assets


@pytest.fixture
def provider(db_session, make_user, tmp_path, monkeypatch):
    directory = local_embedding_assets(tmp_path / "assets")
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", str(directory))
    monkeypatch.setitem(_overlay, "embedding_model_key", "two-tower-contract")
    actor = make_user(superuser=True)
    configuration.update_settings(
        db_session, actor, {"enabled": True, "embeddings_enabled": True}
    )
    return configured_provider(get_session_factory()), actor


@pytest.fixture
def indexed(db_session, provider, make_model, make_file, make_passage_vector):
    native, actor = provider
    generation_id = store.initialize(get_session_factory(), native)
    generation = db_session.get(IndexGeneration, generation_id)
    first, second = make_model(), make_model()
    make_passage_vector(generation, make_file(first))
    make_passage_vector(generation, make_file(second))
    return native, actor, first, second


class TestSemanticSearch:
    def test_initializes_empty_native_index(self, db_session, provider):
        _, actor = provider

        result = search.search(
            db_session, get_session_factory(), actor, search.SearchRequest(text="red")
        )

        assert result["index_state"] == "empty"
        assert result["evidence_kind"] == "semantic"
        assert result["items"] == []

    def test_queries_cached_model_vectors(self, db_session, indexed):
        _, actor, first, second = indexed

        result = search.search(
            db_session,
            get_session_factory(),
            actor,
            search.SearchRequest(model_id=first.id),
        )

        assert [
            (row["model"]["id"], row["score"], row["evidence_kind"])
            for row in result["items"]
        ] == [(second.id, 1.0, "semantic")]
        assert result["scanned"] == 1

    def test_reports_missing_model_vectors(self, db_session, indexed, make_model):
        _, actor, _, _ = indexed
        model = make_model()

        result = search.search(
            db_session,
            get_session_factory(),
            actor,
            search.SearchRequest(model_id=model.id),
        )

        assert result["index_state"] == "missing_model_vectors"
        assert result["items"] == []
        assert result["scanned"] == 0

    def test_rejects_another_space(self, db_session, indexed):
        _, actor, _, _ = indexed

        with pytest.raises(OperationError, match="embedding_space_mismatch"):
            search.search(
                db_session,
                get_session_factory(),
                actor,
                search.SearchRequest(text="red", space_id=999999),
            )

    def test_hides_a_noneditable_query_model(self, db_session, indexed, make_user):
        _, _, first, _ = indexed

        with pytest.raises(OperationError, match="similarity_scope_unavailable"):
            search.search(
                db_session,
                get_session_factory(),
                make_user(),
                search.SearchRequest(model_id=first.id),
            )

    def test_hides_noneditable_text_neighbors(self, db_session, indexed, make_user):
        result = search.search(
            db_session,
            get_session_factory(),
            make_user(),
            search.SearchRequest(text="red"),
        )

        assert result["items"] == []
        assert result["scanned"] == 0

    def test_propagates_native_failure_as_stable_conflict(self, db_session, provider):
        native, actor = provider
        (native.directory / "image.onnx").write_bytes(b"corrupt")

        with pytest.raises(OperationError, match="embedding_asset_digest_mismatch"):
            search.search(
                db_session,
                get_session_factory(),
                actor,
                search.SearchRequest(text="red"),
            )


class TestEmbeddingCapabilities:
    def test_requires_native_validation(self, db_session, provider):
        assert search.capabilities(db_session, get_session_factory()) == {
            "local_embeddings": False,
            "text_to_shape": False,
            "embedding_reason": "embedding_not_validated",
        }

    def test_reports_a_validated_clip_space(self, db_session, indexed):
        assert search.capabilities(db_session, get_session_factory()) == {
            "local_embeddings": True,
            "text_to_shape": True,
            "embedding_reason": None,
        }

    @pytest.mark.parametrize("missing", ["onnxruntime", "tokenizers"])
    def test_reports_missing_native_dependency(
        self, db_session, provider, monkeypatch, missing
    ):
        import importlib.util

        original = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name: None if name == missing else original(name),
        )

        assert search.capabilities(db_session, get_session_factory()) == {
            "local_embeddings": False,
            "text_to_shape": False,
            "embedding_reason": "embedding_runtime_unavailable",
        }
