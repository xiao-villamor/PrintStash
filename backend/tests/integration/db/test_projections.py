"""Batched content changes retain transactional background projection requests."""

import pytest
from sqlmodel import select

from app.db.models import SearchPassage, SearchProjectionRequest
from app.db.projections import (
    batch_content_changes,
    bind_content_projection,
    content_changed,
)
from app.modules.search.projection import LibraryProjection
from tests.search_projection import drain_search


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

        assert len(db_session.exec(select(SearchProjectionRequest)).all()) == 1025
        assert db_session.exec(select(SearchPassage)).all() == []
        drain_search(db_session)
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

        drain_search(db_session)
        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]

    def test_keeps_projection_rollback_in_the_owner(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        content_changed(db_session, "model", [model.id])
        drain_search(db_session)
        with batch_content_changes(db_session):
            model.name = "After"
            db_session.add(model)
            content_changed(db_session, "model", [model.id])
        db_session.rollback()

        assert db_session.exec(select(SearchProjectionRequest)).all() == []
        drain_search(db_session)
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

        drain_search(db_session)
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

        drain_search(db_session)
        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]
