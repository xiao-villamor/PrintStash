"""Content notifications update projections before the source commit returns."""

import pytest
from sqlmodel import delete, select

from app.db.models import ModelTagLink, SearchPassage
from app.db.projections import bind_content_projection, content_changed
from app.modules.library.commands import update_model
from app.modules.search.projection import LibraryProjection
from app.schemas.models import ModelUpdate


@pytest.fixture
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestContentProjection:
    def test_refreshes_every_member_of_a_large_collection(
        self, projection, db_session, make_collection, make_model
    ):
        collection = make_collection("Before")
        models = [
            make_model(f"Part {index}", collection=collection) for index in range(129)
        ]
        content_changed(db_session, "collection", [collection.id])
        collection.name = "After"
        collection.path = "after"
        db_session.add(collection)

        content_changed(db_session, "collection", [collection.id])

        rows = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_type == "model")
        ).all()
        assert {row.subject_id for row in rows} == {model.id for model in models}
        assert all("after" in row.text and "before" not in row.text for row in rows)

    def test_projects_a_newly_attached_provenance_source(
        self, projection, db_session, make_model, make_provenance_source
    ):
        model = make_model("Unnamed")
        content_changed(db_session, "model", [model.id])
        source = make_provenance_source(model, tags=["Flexible hinge"])

        content_changed(db_session, "provenance", [source.id])

        assert "Flexible hinge" in db_session.exec(select(SearchPassage.text)).one()

    def test_preserves_content_when_projection_fails(
        self, db_session, make_model, make_user
    ):
        class BrokenProjection:
            def refresh(self, session, sources):
                raise RuntimeError("projection unavailable")

        model = make_model("Before")
        actor = make_user(superuser=True)
        previous = bind_content_projection(BrokenProjection())
        try:
            with pytest.raises(RuntimeError, match="projection unavailable"):
                update_model(model.id, ModelUpdate(name="After"), actor, db_session)
            db_session.expire_all()
            assert model.name == "Before"
            assert db_session.exec(select(SearchPassage)).all() == []
        finally:
            bind_content_projection(previous)

    def test_projects_an_authorized_model_edit(
        self, projection, db_session, make_model, make_user
    ):
        model = make_model("Before")
        actor = make_user(superuser=True)

        update_model(model.id, ModelUpdate(name="After"), actor, db_session)

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: After"]

    def test_refreshes_descendants_after_an_ancestor_tag_change(
        self,
        projection,
        db_session,
        make_collection,
        make_model,
        make_tag,
        tag_collection,
    ):
        ancestor = make_collection("Root")
        child = make_collection("Child", parent=ancestor)
        model = make_model("Bracket", collection=child)
        content_changed(db_session, "model", [model.id])
        tag_collection(ancestor, make_tag("flexible"))

        content_changed(db_session, "collection", [ancestor.id])

        text = db_session.exec(
            select(SearchPassage.text).where(SearchPassage.subject_type == "model")
        ).one()
        assert "Tags: flexible" in text

    def test_refreshes_removed_relationships(
        self, projection, db_session, make_model, make_tag, tag_model
    ):
        model = make_model("Bracket")
        tag_model(model, make_tag("flexible"))
        content_changed(db_session, "model", [model.id])
        db_session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model.id))

        content_changed(db_session, "model", [model.id])

        assert db_session.exec(select(SearchPassage.text)).one() == "Title: Bracket"

    def test_rolls_back_a_content_notification(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        content_changed(db_session, "model", [model.id])
        db_session.commit()
        model.name = "After"
        db_session.add(model)
        content_changed(db_session, "model", [model.id])

        db_session.rollback()

        assert db_session.exec(select(SearchPassage.text)).one() == "Title: Before"

    def test_leaves_content_usable_without_a_projection(self, db_session, make_model):
        previous = bind_content_projection(None)
        model = make_model("Bracket")

        content_changed(db_session, "model", [model.id])
        db_session.commit()

        assert db_session.exec(select(SearchPassage)).all() == []
        bind_content_projection(previous)
