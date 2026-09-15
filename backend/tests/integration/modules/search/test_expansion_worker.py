"""Durable sparse indexing fences source, actor and configuration changes."""

from datetime import timedelta

import pytest
from printstash_core.inference import EmbeddingError
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import update
from sqlmodel import select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SearchExpansion,
    SearchExpansionTerm,
    SearchPassage,
    SystemConfig,
    User,
)
from app.db.session import get_session_factory
from app.modules.inference.sparse import LocalSparseProvider
from app.modules.search import configuration
from app.modules.search.expansion_worker import ExpansionProcessor
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search


class TestExpansionProcessor:
    def test_materializes_opted_in_passages(self, db_session, sparse_setup, make_model):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        db_session.commit()
        assert ExpansionProcessor(get_session_factory()).work_one()
        db_session.expire_all()
        row = db_session.exec(select(SearchExpansion)).one()
        assert row.phase == "ready"
        assert row.recipe == model.id
        assert (
            row.input_hash == db_session.get(SearchPassage, row.passage_id).content_hash
        )
        assert [
            term.term
            for term in db_session.exec(
                select(SearchExpansionTerm).order_by(SearchExpansionTerm.term)
            ).all()
        ] == ["bicycle", "bike"]
        assert [
            item.subject_id for item in search(db_session, actor, "bike").items
        ] == [subject.id]
        assert not ExpansionProcessor(get_session_factory()).work_one()

    def test_retains_original_search_during_backfill(
        self, db_session, sparse_setup, make_model
    ):
        actor, _ = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        db_session.commit()
        assert [
            item.subject_id for item in search(db_session, actor, "bicycle").items
        ] == [subject.id]
        assert search(db_session, actor, "bike").items == []

    @pytest.mark.parametrize(
        "flag", ["enabled", "sparse_expansion_enabled", "local_models_enabled"]
    )
    def test_does_no_work_with_an_opt_in_disabled(
        self, db_session, sparse_setup, make_model, flag
    ):
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        row = db_session.get(SystemConfig, 1)
        row.ai_search_settings_json = (
            configuration.settings(db_session)
            .model_copy(update={flag: False})
            .model_dump_json()
        )
        db_session.add(row)
        db_session.commit()
        assert not ExpansionProcessor(get_session_factory()).work_one()
        assert db_session.exec(select(SearchExpansion)).all() == []

    @pytest.mark.parametrize("change", ["source", "disabled", "actor", "trash"])
    def test_discards_late_results(self, db_session, sparse_setup, make_model, change):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        db_session.commit()
        sessions = get_session_factory()
        native = LocalSparseProvider(
            sessions, model.directory, model.manifest.model_key, 1
        )

        class ChangedDuringInference:
            def expand(self, text, *, context):
                result = native.expand(text, context=context)
                with sessions.scoped_session() as session:
                    if change == "source":
                        session.exec(
                            update(SearchPassage).values(content_hash="e" * 64)
                        )
                    elif change == "actor":
                        session.exec(
                            update(User)
                            .where(User.id == actor.id)
                            .values(is_active=False)
                        )
                    elif change == "trash":
                        from app.db.models import Model

                        session.exec(
                            update(Model)
                            .where(Model.id == subject.id)
                            .values(deleted_at=utcnow())
                        )
                    else:
                        row = session.get(SystemConfig, 1)
                        row.ai_search_settings_json = (
                            configuration.settings(session)
                            .model_copy(update={"sparse_expansion_enabled": False})
                            .model_dump_json()
                        )
                        session.add(row)
                    session.commit()
                return result

        assert ExpansionProcessor(
            sessions, provider_factory=lambda *_: ChangedDuringInference()
        ).work_one()
        db_session.expire_all()
        assert db_session.exec(select(SearchExpansionTerm)).all() == []

    def test_retries_an_expired_lease(
        self, db_session, sparse_setup, make_model, make_search_expansion
    ):
        _, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        passage = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_type == "model")
        ).one()
        make_search_expansion(
            passage,
            recipe=model.id,
            phase="running",
            token="old",
            attempts=1,
            lease_until=utcnow() - timedelta(seconds=1),
        )
        db_session.commit()
        assert ExpansionProcessor(get_session_factory()).work_one()
        db_session.expire_all()
        row = db_session.get(SearchExpansion, passage.id)
        assert (row.phase, row.attempts, row.token) == ("ready", 2, None)

    def test_quarantines_an_exhausted_lease(
        self, db_session, sparse_setup, make_model, make_search_expansion
    ):
        _, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        passage = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_type == "model")
        ).one()
        make_search_expansion(
            passage,
            recipe=model.id,
            phase="running",
            token="old",
            attempts=3,
            lease_until=utcnow() - timedelta(seconds=1),
        )
        db_session.commit()
        assert not ExpansionProcessor(get_session_factory()).work_one()
        db_session.expire_all()
        row = db_session.get(SearchExpansion, passage.id)
        assert (row.phase, row.attempts, row.token) == ("failed", 3, None)

    def test_records_only_a_safe_failure_code(
        self, db_session, sparse_setup, make_model
    ):
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        db_session.commit()

        class Unavailable:
            def expand(self, text, *, context):
                raise RuntimeError("private passage: " + text)

        assert ExpansionProcessor(
            get_session_factory(), provider_factory=lambda *_: Unavailable()
        ).work_one()
        row = db_session.exec(select(SearchExpansion)).one()
        assert row.phase == "failed"
        assert row.error_code == "embedding_sparse_failed"
        assert ensure_utc(row.retry_at) > utcnow()
        assert db_session.exec(select(SearchExpansionTerm)).all() == []

    def test_keeps_an_enabled_model_in_the_cache(self, db_session, sparse_setup):
        from app.modules.inference.model_cache import remove

        _, model = sparse_setup
        with pytest.raises(EmbeddingError, match="embedding_model_in_use"):
            remove(db_session, model.id)
        assert model.directory.is_dir()

    def test_preserves_retry_budget_under_compute_backpressure(
        self, db_session, sparse_setup, make_model
    ):
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        db_session.commit()

        class Busy:
            def expand(self, text, *, context):
                raise EmbeddingError("embedding_compute_busy")

        assert ExpansionProcessor(
            get_session_factory(), provider_factory=lambda *_: Busy()
        ).work_one()
        row = db_session.exec(select(SearchExpansion)).one()
        assert row.attempts == 0
        assert row.phase == "failed"
        assert row.error_code == "embedding_compute_busy"

    @pytest.mark.parametrize("owner_trashed", [True, False])
    def test_skips_nonlive_contributors(
        self, db_session, sparse_setup, make_model, make_search_passage, owner_trashed
    ):
        live = make_model("bicycle", deleted_at=utcnow() if owner_trashed else None)
        hidden = make_model("private", deleted_at=utcnow())
        make_search_passage(
            SearchSubject(SubjectType.MODEL, live.id),
            text="bicycle",
            access_dependencies_json="[]"
            if owner_trashed
            else f'[["model",{hidden.id}]]',
        )
        db_session.commit()
        assert not ExpansionProcessor(get_session_factory()).work_one()
        assert db_session.exec(select(SearchExpansionTerm)).all() == []

    def test_defers_expansion_when_the_shared_index_budget_is_full(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_passage,
        make_search_expansion,
    ):
        _, model = sparse_setup
        for _ in range(32):
            subject = make_model("bicycle")
            passage = make_search_passage(
                SearchSubject(SubjectType.MODEL, subject.id), text="bicycle"
            )
            make_search_expansion(passage, recipe=model.id)
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        configuration.update(
            db_session,
            configuration.settings(db_session).model_copy(
                update={"max_index_bytes": 1048576}
            ),
        )
        db_session.commit()
        assert ExpansionProcessor(get_session_factory()).work_one()
        row = db_session.exec(
            select(SearchExpansion).where(SearchExpansion.phase == "failed")
        ).one()
        assert row.error_code == "storage_capacity_exceeded"
        assert row.attempts == 0
        assert db_session.exec(select(SearchExpansionTerm)).all() == []
