"""Scale replicas remain ordinary searchable Models through a real refresh."""

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import Model, PassageVector, SearchPassage
from app.db.models.search import (
    SearchLexicalPosting,
    SearchLexicalState,
    SearchLexicalTerm,
)
from app.modules.search import lexical_index, vector_index
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search
from tests.factories import (
    build_embedding_space,
    build_index_generation,
    build_model,
    build_passage_vector,
    build_user,
)
from tests.factories.search_scale import replicate_models
from tests.fakes.search_scale import index_rows, prepare_indexes


class TestReplicateModels:
    def test_checks_native_cardinality_after_seeding_session_expires(
        self, db_session, monkeypatch
    ):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
        generation = build_index_generation(
            db_session, build_embedding_space(db_session), index_backend="sqlite_vec"
        )
        seed = build_model(db_session, "Assembly bracket")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, seed.id))
        passage = db_session.exec(select(SearchPassage)).one()
        build_passage_vector(db_session, generation, passage=passage)
        assert prepare_indexes(db_session, generation, count=1) == 1
        generation_id = generation.id
        db_session.commit()
        db_session.expire(generation)
        db_session.expunge(generation)
        with Session(db_session.get_bind()) as fresh:
            assert index_rows(fresh, generation_id, count=1) == 1

    def test_rebuilds_the_complete_native_benchmark_fixture(
        self, db_session, monkeypatch
    ):
        from sqlalchemy import column, table

        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
        generation = build_index_generation(
            db_session, build_embedding_space(db_session), index_backend="sqlite_vec"
        )
        seed = build_model(db_session, "Boat", description="Calibration boat")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, seed.id))
        passage = db_session.exec(select(SearchPassage)).one()
        build_passage_vector(db_session, generation, passage=passage)
        assert vector_index.prepare(db_session, generation)
        while vector_index.rebuild_partition(db_session, generation):
            pass
        assert generation.index_state == "ready"
        replicate_models(db_session, [seed], generation, count=10)
        assert prepare_indexes(db_session, generation, count=10) == 10
        name = vector_index.table_name(generation.id, "sqlite")
        native = table(name, column("rowid"))
        assert set(db_session.execute(select(native.c.rowid)).scalars()) == set(
            db_session.exec(select(PassageVector.id)).all()
        )

    def test_builds_an_unembedded_backfill_corpus(self, db_session):
        actor = build_user(db_session, superuser=True)
        seed = build_model(db_session, "Assembly bracket")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, seed.id))
        replicas = replicate_models(db_session, [seed], None, count=10)
        db_session.commit()
        assert replicas["replicas"] == 9
        assert db_session.exec(select(PassageVector)).all() == []
        assert db_session.exec(select(func.count(SearchPassage.id))).one() == 10
        assert len(search(db_session, actor, "bracket", mode="lexical").items) == 10

    def test_replicas_retain_searchability_after_maintenance(
        self,
        db_session,
    ):
        actor = build_user(db_session, superuser=True)
        generation = build_index_generation(
            db_session, build_embedding_space(db_session)
        )
        seeds = [
            build_model(db_session, "Boat", description="Calibration boat"),
            build_model(db_session, "Bracket", description="Shelf support"),
        ]
        for model in seeds:
            sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
            passage = db_session.exec(
                select(SearchPassage).where(SearchPassage.subject_id == model.id)
            ).one()
            build_passage_vector(db_session, generation, passage=passage)
        result = replicate_models(db_session, seeds, generation, count=10)
        db_session.commit()
        replica_id = result["first_replica_id"]
        replica = db_session.get(Model, replica_id)
        assert replica.name == seeds[0].name
        assert replica.hash != seeds[0].hash
        passage = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_id == replica_id)
        ).one()
        vector = db_session.exec(
            select(PassageVector).where(PassageVector.passage_id == passage.id)
        ).one()
        before = (passage.content_hash, vector.id, vector.vector_blob)
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, replica_id))
        db_session.commit()
        db_session.expire_all()
        vector = db_session.exec(
            select(PassageVector).where(PassageVector.passage_id == passage.id)
        ).one()
        assert (passage.content_hash, vector.id, vector.vector_blob) == before
        while lexical_index.rebuild_partition(db_session, limit=3):
            db_session.commit()
        db_session.commit()
        state = db_session.get(SearchLexicalState, 1)
        assert state.document_count == 10
        assert (
            state.total_length
            == db_session.exec(select(func.sum(SearchPassage.token_count))).one()
        )
        assert state.native_phase == "ready"
        assert dict(
            db_session.exec(
                select(SearchLexicalTerm.term, SearchLexicalTerm.document_frequency)
            ).all()
        ) == dict(
            db_session.exec(
                select(SearchLexicalPosting.term, func.count()).group_by(
                    SearchLexicalPosting.term
                )
            ).all()
        )
        found = search(db_session, actor, "boat", mode="lexical")
        assert found.lexical_backend == "fts5"
        assert {row.subject_id for row in found.items} == {
            seeds[0].id,
            replica_id,
            replica_id + 2,
            replica_id + 4,
            replica_id + 6,
        }
