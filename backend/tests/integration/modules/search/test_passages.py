"""Durable passage refresh is idempotent and belongs to the content transaction.

Old recipes survive live refreshes; absence removes every recipe. No inference
or user-facing retrieval is implied by these internal persistence contracts.
"""

from datetime import datetime

import pytest
from printstash_core.search.passages import RECIPE_VERSION, SearchSubject, SubjectType
from sqlmodel import select

from app.db.models import PassageVector
from app.db.models.search import SearchPassage
from app.modules.search.passages import PassageChanges, sync_subject


@pytest.fixture
def indexed_model(
    db_session,
    make_model,
    make_file,
    make_embedding_space,
    make_index_generation,
    make_passage_vector,
):
    model = make_model("Dragon")
    file = make_file(model)
    subject = SearchSubject(SubjectType.MODEL, model.id)
    sync_subject(db_session, subject)
    db_session.commit()
    passage = db_session.exec(select(SearchPassage)).one()
    generation = make_index_generation(make_embedding_space())
    vector = make_passage_vector(
        generation,
        file,
        passage_id=passage.id,
        unit_kind="passage",
        unit_key=f"passage:{passage.id}",
        input_hash=passage.content_hash,
    )
    return model, subject, vector.id


class TestSyncSubject:
    def test_invalidates_vectors_when_indexed_content_changes(
        self, db_session, indexed_model
    ):
        model, subject, vector_id = indexed_model
        model.description = "Changed source text"
        db_session.add(model)

        sync_subject(db_session, subject)
        db_session.commit()

        assert db_session.get(PassageVector, vector_id, populate_existing=True) is None

    def test_preserves_vectors_for_nonindexed_edits(self, db_session, indexed_model):
        model, subject, vector_id = indexed_model
        model.thumbnail_path = "replacement.webp"
        db_session.add(model)

        sync_subject(db_session, subject)
        db_session.commit()

        assert (
            db_session.get(PassageVector, vector_id, populate_existing=True) is not None
        )

    def test_rolls_back_vector_invalidation_with_content(
        self, db_session, indexed_model
    ):
        model, subject, vector_id = indexed_model
        model.description = "Uncommitted change"
        db_session.add(model)

        sync_subject(db_session, subject)
        db_session.rollback()

        assert (
            db_session.get(PassageVector, vector_id, populate_existing=True) is not None
        )

    def test_persists_model_text(self, db_session, make_model):
        model = make_model("Dragon", description="No supports")

        changes = sync_subject(db_session, SearchSubject(SubjectType.MODEL, model.id))
        db_session.commit()
        row = db_session.exec(select(SearchPassage)).one()

        assert changes == PassageChanges(inserted=1)
        assert row.text == "Title: Dragon\nDescription: No supports"
        assert row.access_dependencies_json == "[]"

    def test_preserves_idempotent_passage_identity(self, db_session, make_model):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        sync_subject(db_session, subject)
        db_session.commit()
        row = db_session.exec(select(SearchPassage)).one()
        before = (row.id, row.content_hash, row.updated_at)

        changes = sync_subject(db_session, subject)
        db_session.commit()
        db_session.refresh(row)

        assert changes == PassageChanges()
        assert (row.id, row.content_hash, row.updated_at) == before

    def test_ignores_nonindexed_edits_for_content_freshness(
        self, db_session, make_model
    ):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        sync_subject(db_session, subject)
        row = db_session.exec(select(SearchPassage)).one()
        before = (row.id, row.content_hash, row.updated_at)
        model.updated_at = datetime(2026, 9, 12)
        model.thumbnail_path = "new-thumbnail.webp"
        db_session.add(model)

        changes = sync_subject(db_session, subject)
        db_session.commit()

        assert changes == PassageChanges()
        assert (row.id, row.content_hash, row.updated_at) == before

    def test_replaces_changed_passage_content(self, db_session, make_model):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        sync_subject(db_session, subject)
        row = db_session.exec(select(SearchPassage)).one()
        original_id, original_hash = row.id, row.content_hash
        model.description = "Prints without supports"
        db_session.add(model)

        changes = sync_subject(db_session, subject)
        db_session.commit()

        assert changes == PassageChanges(updated=1)
        assert row.id == original_id
        assert row.content_hash != original_hash
        assert row.text.endswith("Description: Prints without supports")

    def test_removes_obsolete_passage_windows(self, db_session, make_document):
        document = make_document(body="word " * 500)
        subject = SearchSubject(SubjectType.DOCUMENT, document.id)
        sync_subject(db_session, subject)
        document.body = "Short guide"
        db_session.add(document)

        changes = sync_subject(db_session, subject)

        assert changes == PassageChanges(updated=1, removed=1)
        assert db_session.exec(select(SearchPassage.text)).all() == [
            "Title: manual\nBody: Short guide"
        ]

    def test_preserves_other_live_recipes(
        self, db_session, make_model, make_search_passage
    ):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        old = make_search_passage(
            subject, recipe_version=RECIPE_VERSION + 2, text="Other recipe"
        )

        sync_subject(db_session, subject)
        db_session.commit()

        assert db_session.get(SearchPassage, old.id).text == "Other recipe"
        assert len(db_session.exec(select(SearchPassage)).all()) == 2

    def test_removes_every_recipe_for_a_trashed_subject(
        self,
        db_session,
        make_model,
        make_search_passage,
        make_embedding_space,
        make_index_generation,
        make_passage_vector,
        make_user,
    ):
        from app.db.models import PassageVector
        from app.modules.search.retrieval import search

        actor = make_user(superuser=True)
        model = make_model("Dragon", trashed=True)
        subject = SearchSubject(SubjectType.MODEL, model.id)
        generation = make_index_generation(make_embedding_space())
        for version in (RECIPE_VERSION, RECIPE_VERSION + 1):
            passage = make_search_passage(subject, recipe_version=version)
            make_passage_vector(generation, passage=passage)

        changes = sync_subject(db_session, subject)

        assert changes == PassageChanges(removed=2)
        assert db_session.exec(select(SearchPassage)).all() == []
        assert db_session.exec(select(PassageVector)).all() == []
        assert search(db_session, actor, "Stored passage").items == []

    def test_removes_passages_for_a_purged_subject(
        self, db_session, make_model, make_search_passage
    ):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        make_search_passage(subject)
        db_session.delete(model)
        db_session.commit()

        changes = sync_subject(db_session, subject)

        assert changes == PassageChanges(removed=1)
        assert db_session.exec(select(SearchPassage)).all() == []

    def test_restores_passages_for_a_restored_subject(
        self, db_session, make_model, make_user
    ):
        from app.modules.search.retrieval import search

        actor = make_user(superuser=True)
        model = make_model("Dragon", trashed=True)
        subject = SearchSubject(SubjectType.MODEL, model.id)
        sync_subject(db_session, subject)
        model.deleted_at = None
        db_session.add(model)

        changes = sync_subject(db_session, subject)

        assert changes == PassageChanges(inserted=1)
        assert db_session.exec(select(SearchPassage.text)).one() == "Title: Dragon"
        db_session.commit()
        result = search(db_session, actor, "Dragon", mode="lexical")
        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            ("model", model.id)
        ]

    def test_rolls_projection_back_with_content(self, db_session, make_model):
        model = make_model("Dragon")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        sync_subject(db_session, subject)
        db_session.commit()
        model.description = "Uncommitted description"
        db_session.add(model)
        sync_subject(db_session, subject)

        db_session.rollback()
        db_session.refresh(model)

        assert model.description is None
        assert db_session.exec(select(SearchPassage.text)).one() == "Title: Dragon"

    def test_does_not_create_rows_for_a_missing_subject(self, db_session):
        changes = sync_subject(db_session, SearchSubject(SubjectType.MODEL, 123456))

        assert changes == PassageChanges()
        assert db_session.exec(select(SearchPassage)).all() == []
