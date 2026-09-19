"""PostgreSQL authorizes every passage contributor before dense scoring."""

import json
from dataclasses import asdict
from unittest.mock import patch
from uuid import uuid4

import pytest
from printstash_core.inference import EmbeddingSpace as Space
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import create_engine, make_url
from sqlmodel import Session, SQLModel, select

from app.core.config import _overlay
from app.db.models import SearchPassage
from app.db.url import normalize_database_url
from app.modules.inference.configuration import load
from app.modules.inference.query import close_queries
from app.modules.search import configuration, vector_index
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from app.modules.search.text_inputs import TextRecipe
from app.schemas.inference import SearchSettings
from tests.containers import pgvector_url
from tests.factories import (
    build_collection,
    build_document,
    build_embedding_space,
    build_index_generation,
    build_inference_endpoint,
    build_passage_vector,
    build_search_passage,
    build_user,
    grant_collection_role,
)


@pytest.fixture(params=["numpy", "pgvector"])
def semantic_database(request, monkeypatch):
    close_queries()
    monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
    schema = "semantic_" + uuid4().hex
    root = make_url(normalize_database_url(pgvector_url()))
    base = create_engine(root)
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(
        root.update_query_dict(
            {"options": f"-csearch_path={schema},public -cstatement_timeout=10000"}
        )
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            reader = build_user(session)
            public = build_collection(session, "Shared")
            private = build_collection(session, "Private")
            grant_collection_role(session, reader, public)
            guide = build_document(session, "Assembly guide", collection_id=public.id)
            secret = build_document(
                session, "Secretprototype", collection_id=private.id
            )
            for document in (guide, secret):
                sync_subject(session, SearchSubject(SubjectType.DOCUMENT, document.id))
            build_search_passage(
                session,
                SearchSubject(SubjectType.DOCUMENT, guide.id),
                text="Secretprototype contributor",
                title="Secretprototype",
                visibility_segment_key="f" * 64,
                access_dependencies_json=json.dumps([["document", secret.id]]),
            )
            endpoint = build_inference_endpoint(session)
            config = load(endpoint)
            contract = Space(
                config.model,
                config.revision,
                4,
                "text",
                TextRecipe().encode(),
                provider="openai_compatible",
                profile="semantic_text",
                provider_config_hash=config.identity,
            )
            space = build_embedding_space(
                session,
                native_dimension=4,
                modality="text",
                profile="semantic_text",
                provider="openai_compatible",
                config_hash=contract.config_hash,
                config_json=json.dumps(asdict(contract)),
            )
            generation = build_index_generation(
                session, space, index_backend=request.param
            )
            for passage in session.exec(select(SearchPassage)).all():
                build_passage_vector(session, generation, passage=passage)
            configuration.update(session, SearchSettings(enabled=True))
            vector_index.prepare(session, generation)
            vector_index.rebuild_partition(session, generation)
            session.commit()
            with patch(
                "app.modules.inference.remote.post_json",
                return_value={"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]},
            ):
                yield session, reader, guide, secret
    finally:
        close_queries()
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestSearch:
    def test_excludes_hidden_contributors_from_dense_evidence(self, semantic_database):
        session, reader, guide, secret = semantic_database

        result = search(session, reader, "how to assemble")

        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            (SubjectType.DOCUMENT, guide.id)
        ]
        assert result.semantic_ready
        assert "Secretprototype" not in result.model_dump_json()

    def test_never_explains_a_hidden_keyword_match(self, semantic_database):
        session, reader, guide, secret = semantic_database

        result = search(session, reader, "Secretprototype")

        assert [item.subject_id for item in result.items] == [guide.id]
        assert all(
            evidence.leg == "semantic_text"
            for item in result.items
            for evidence in item.evidence
        )
        assert "Secretprototype" not in result.model_dump_json()
