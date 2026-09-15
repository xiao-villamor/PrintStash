"""Separate captions keep human text intact and bind to exactly one real owner."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import SubjectCaption


class TestSubjectCaption:
    @pytest.mark.parametrize("kind", list(SubjectType))
    def test_binds_caption_lifetime_to_its_subject(
        self,
        db_session,
        make_model,
        make_collection,
        make_multipart_model,
        make_document,
        make_subject_caption,
        kind,
    ):
        builders = {
            SubjectType.MODEL: make_model,
            SubjectType.COLLECTION: make_collection,
            SubjectType.MULTIPART_MODEL: make_multipart_model,
            SubjectType.DOCUMENT: make_document,
        }
        subject = builders[kind]("Human title")
        caption = make_subject_caption(
            SearchSubject(kind, subject.id), text="Separate AI text"
        )
        assert (caption.subject_type, caption.subject_id, caption.text) == (
            kind.value,
            subject.id,
            "Separate AI text",
        )
        db_session.exec(delete(type(subject)).where(type(subject).id == subject.id))
        db_session.commit()
        assert db_session.exec(select(SubjectCaption)).all() == []

    @pytest.mark.parametrize(
        "invalid", ["missing_owner", "wrong_owner", "too_long", "dismissed_text"]
    )
    def test_rejects_invalid_caption_state(
        self, db_session, make_model, make_subject_caption, invalid
    ):
        model = make_model()
        changes = {
            "missing_owner": {"model_id": None},
            "wrong_owner": {"subject_id": model.id + 1000},
            "too_long": {"text": "x" * 2049},
            "dismissed_text": {"state": "dismissed", "text": "must disappear"},
        }[invalid]
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            make_subject_caption(SearchSubject(SubjectType.MODEL, model.id), **changes)
        db_session.rollback()
        assert db_session.exec(select(SubjectCaption)).all() == []
