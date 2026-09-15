"""Batched content changes are projected before the owning transaction commits."""

import pytest
from sqlmodel import select

from app.db.models import SearchPassage
from app.db.projections import (
    batch_content_changes,
    bind_content_projection,
    content_changed,
)
from app.modules.search.projection import LibraryProjection


@pytest.fixture
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestBatchContentChanges:
    def test_preserves_all_content_across_the_batch_limit(
        self, projection, db_session, make_model
    ):
        models = [make_model(f"Part {index}") for index in range(1025)]
        with batch_content_changes(db_session):
            content_changed(db_session, "model", [model.id for model in models])
            models[0].name = "Revised part"
            db_session.add(models[0])
            content_changed(db_session, "model", [models[0].id])

        assert set(db_session.exec(select(SearchPassage.text)).all()) == {
            "Title: Revised part",
            *(f"Title: Part {index}" for index in range(1, 1025)),
        }

    def test_projects_the_final_staged_content(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        with batch_content_changes(db_session):
            content_changed(db_session, "model", [model.id])
            model.name = "After"
            db_session.add(model)
            content_changed(db_session, "model", [model.id])

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]

    def test_keeps_projection_rollback_in_the_owner(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        content_changed(db_session, "model", [model.id])
        db_session.commit()
        with batch_content_changes(db_session):
            model.name = "After"
            db_session.add(model)
            content_changed(db_session, "model", [model.id])
        db_session.rollback()

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: Before"]

    def test_releases_a_failed_batch(self, projection, db_session, make_model):
        model = make_model("Before")
        with pytest.raises(RuntimeError, match="aborted"):
            with batch_content_changes(db_session):
                content_changed(db_session, "model", [model.id])
                raise RuntimeError("aborted")
        db_session.rollback()
        model.name = "After"
        db_session.add(model)
        content_changed(db_session, "model", [model.id])

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]

    def test_nested_batches_publish_the_outer_result(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        with batch_content_changes(db_session):
            with batch_content_changes(db_session):
                content_changed(db_session, "model", [model.id])
            model.name = "After"
            db_session.add(model)
            content_changed(db_session, "model", [model.id])

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]
