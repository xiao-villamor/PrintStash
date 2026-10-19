"""Optional tag filters preserve the unfiltered library selection."""

import pytest
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import Collection, Model
from app.db.scopes import live
from app.modules.library.library_search import apply_library_search, effective_tag_pairs


class TestApplyLibrarySearch:
    @pytest.mark.parametrize("tag_slugs", [[], ["", "  "]], ids=["absent", "blank"])
    def test_preserves_models_without_an_effective_tag_filter(
        self, db_session, make_model, make_tag, tag_model, tag_slugs
    ):
        tagged = make_model("Tagged")
        plain = make_model("Plain")
        tag_model(tagged, make_tag("hardware"))

        statement = apply_library_search(
            select(Model).where(live(Model), Model.id.in_([tagged.id, plain.id])),
            query=None,
            tag_slugs=tag_slugs,
        )
        rows = db_session.exec(statement).all()

        assert {row.id for row in rows} == {tagged.id, plain.id}


class TestEffectiveTagPairs:
    @pytest.mark.parametrize("association", ["model", "file", "collection"], ids=str)
    def test_reads_newly_linked_tags_after_an_earlier_read(
        self,
        db_session,
        make_collection,
        make_model,
        make_file,
        make_tag,
        tag_model,
        tag_file,
        tag_collection,
        association,
    ):
        collection = make_collection("Parent")
        child = make_collection("Child", parent=collection)
        model = make_model("Part", collection=child)
        artifact = make_file(model)
        tag = make_tag("new")
        statement = effective_tag_pairs()
        assert db_session.exec(statement).all() == []

        if association == "model":
            tag_model(model, tag)
        elif association == "file":
            tag_file(artifact, tag)
        else:
            tag_collection(collection, tag)

        assert db_session.exec(statement).all() == [(tag.id, model.id)]

    def test_excludes_a_newly_trashed_tag_after_an_earlier_read(
        self,
        db_session,
        make_model,
        make_tag,
        tag_model,
    ):
        model = make_model("Part")
        tag = make_tag("live")
        tag_model(model, tag)
        statement = effective_tag_pairs()
        assert db_session.exec(statement).all() == [(tag.id, model.id)]

        tag.deleted_at = utcnow()
        db_session.add(tag)
        db_session.commit()

        assert db_session.exec(statement).all() == []

    def test_includes_a_restored_tag_after_an_earlier_read(
        self,
        db_session,
        make_model,
        make_tag,
        tag_model,
    ):
        model = make_model("Part")
        tag = make_tag("restorable", deleted_at=utcnow())
        tag_model(model, tag)
        statement = effective_tag_pairs()
        assert db_session.exec(statement).all() == []

        tag.deleted_at = None
        db_session.add(tag)
        db_session.commit()

        assert db_session.exec(statement).all() == [(tag.id, model.id)]

    def test_scopes_each_read_to_its_current_collection_selection(
        self,
        db_session,
        make_collection,
        make_model,
        make_tag,
        tag_model,
    ):
        left = make_collection("Left")
        right = make_collection("Right")
        left_model = make_model("Left part", collection=left)
        right_model = make_model("Right part", collection=right)
        tag = make_tag("shared")
        tag_model(left_model, tag)
        tag_model(right_model, tag)
        expected = {(tag.id, left_model.id), (tag.id, right_model.id)}
        assert set(db_session.exec(effective_tag_pairs()).all()) == expected

        for collection, model in ((left, left_model), (right, right_model)):
            scope = select(Collection.id).where(Collection.id == collection.id)
            assert db_session.exec(effective_tag_pairs(scope)).all() == [
                (tag.id, model.id)
            ]

        assert set(db_session.exec(effective_tag_pairs()).all()) == expected

    def test_reads_rolled_back_association_state(
        self,
        db_session,
        make_model,
        make_tag,
        tag_model,
    ):
        model = make_model("Part")
        tag = make_tag("durable")
        tag_model(model, tag)
        expected = [(tag.id, model.id)]
        statement = effective_tag_pairs()
        assert db_session.exec(statement).all() == expected
        tag.deleted_at = utcnow()
        db_session.add(tag)
        db_session.flush()
        assert db_session.exec(statement).all() == []

        db_session.rollback()

        assert db_session.exec(statement).all() == expected
