"""Capability and backlog reporting obey the same subject visibility as search."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType

from app.db.models import EmbeddingSpace, IndexGeneration
from app.modules.search import generations
from app.modules.search.passages import sync_subject
from app.schemas.search_generations import GenerationProposal


class TestStatus:
    @pytest.mark.parametrize("broken", ["{", "[]"])
    def test_degrades_corrupt_active_metadata(
        self, db_session, warm_model, make_user, broken
    ):
        from app.modules.search.status import read

        _, generation = warm_model
        actor = make_user(superuser=True)
        stored = db_session.get(EmbeddingSpace, generation.space_id)
        stored.config_json = broken
        db_session.add(stored)
        db_session.commit()
        result = read(db_session, actor)
        assert result.legs == ["lexical"]
        assert result.degraded == ["search_semantic_unavailable"]
        assert result.semantic_ready is False

    def test_reports_a_missing_local_runtime(
        self, db_session, warm_model, make_user, monkeypatch
    ):
        from app.modules.search import status

        original = status.importlib.util.find_spec
        monkeypatch.setattr(
            status.importlib.util,
            "find_spec",
            lambda name: None if name == "onnxruntime" else original(name),
        )
        result = status.read(db_session, make_user(superuser=True))
        assert result.legs == ["lexical"]
        assert result.degraded == ["search_semantic_unavailable"]
        assert result.semantic_ready is False

    def test_reports_only_usable_active_capabilities(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        from app.modules.search.status import read

        actor, endpoint = generation_setup
        generation = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )
        assert read(db_session, actor).semantic_ready is False
        advance_generation(generation.id)
        result = read(db_session, actor)
        assert result.semantic_ready is True
        assert result.remote_hosts == ["inference.test"]
        assert result.backlog is False

    def test_hides_backlog_for_inaccessible_subjects(
        self,
        db_session,
        generation_setup,
        healthy_embeddings,
        advance_generation,
        make_user,
        make_collection,
        make_model,
    ):
        from app.modules.search.status import read

        actor, endpoint = generation_setup
        generation = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )
        advance_generation(generation.id)
        viewer = make_user()
        model = make_model("Confidential", collection=make_collection("Private"))
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
        assert read(db_session, viewer).backlog is False

    def test_reports_authorized_backlog(
        self,
        db_session,
        generation_setup,
        healthy_embeddings,
        advance_generation,
        make_document,
    ):
        from app.modules.search.status import read

        actor, endpoint = generation_setup
        generation = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )
        advance_generation(generation.id)
        document = make_document("New guide")
        sync_subject(db_session, SearchSubject(SubjectType.DOCUMENT, document.id))
        assert read(db_session, actor).backlog is True

    def test_reports_a_missing_active_endpoint(
        self, db_session, generation_setup, healthy_embeddings, advance_generation
    ):
        from app.modules.search.status import read

        actor, endpoint = generation_setup
        generation = generations.prepare(
            db_session, actor, GenerationProposal(endpoint_id=endpoint.id)
        )
        advance_generation(generation.id)
        db_session.delete(endpoint)
        db_session.flush()
        result = read(db_session, actor)
        assert result.semantic_ready is False
        assert result.degraded == ["search_semantic_unavailable"]
        assert db_session.get(IndexGeneration, generation.id).state == "active"

    def test_reports_pending_lexical_projection(self, db_session, make_user, make_model, make_search_projection_request):
        from app.db.projections import ContentSource
        from app.modules.search.status import read
        model = make_model()
        make_search_projection_request(ContentSource("model", model.id))
        assert read(db_session, make_user(superuser=True)).backlog is True

    def test_hides_private_lexical_backlog(self, db_session, make_user, make_model, make_collection, make_search_projection_request):
        from app.db.projections import ContentSource
        from app.modules.search.status import read
        model = make_model(collection=make_collection("Private"))
        make_search_projection_request(ContentSource("model", model.id))
        assert read(db_session, make_user()).backlog is False
