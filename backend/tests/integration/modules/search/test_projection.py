"""Content commits record durable work; bounded workers project the latest state.

A page is read on a snapshot (``prepare_pending``) and written in a short
transaction (``apply_prepared``) that only goes ahead while its request is
still as it was read. When this goes red, either the projection holds the
database's writer while it reads the library, or a page read before a change
can overwrite what that change asked for.
"""

import pytest
from sqlalchemy import event
from sqlmodel import delete, select

from app.db.models import ModelTagLink, SearchPassage, SearchProjectionRequest
from app.db.projections import bind_content_projection, content_changed
from app.modules.library.commands import update_model
from app.modules.search.projection import (
    LibraryProjection,
    apply_prepared,
    prepare_pending,
    process_pending,
)
from app.schemas.models import ModelUpdate


@pytest.fixture
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


def _drain(session):
    session.commit()
    for _ in range(100):
        changed = process_pending(session)
        session.commit()
        if not changed:
            assert session.exec(select(SearchProjectionRequest)).all() == []
            return
    raise AssertionError("projection did not drain")


class TestContentProjection:
    def test_coalesces_repeated_source_notifications(
        self, projection, db_session, make_model
    ):
        model = make_model("Bracket")

        content_changed(db_session, "model", [model.id])
        content_changed(db_session, "model", [model.id])
        db_session.commit()

        row = db_session.exec(select(SearchProjectionRequest)).one()
        assert (row.source_kind, row.source_id, row.revision) == ("model", model.id, 2)

    def test_rolls_back_uncommitted_work_registration(
        self, projection, db_session, make_model
    ):
        model = make_model("Bracket")

        content_changed(db_session, "model", [model.id])
        db_session.rollback()

        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_hides_stale_text_until_the_change_is_projected(
        self, projection, db_session, make_model, make_user
    ):
        from app.modules.search.access import visible_passage_ids

        model = make_model("Before")
        user = make_user(superuser=True)
        content_changed(db_session, "model", [model.id])
        _drain(db_session)
        model.name = "After"
        db_session.add(model)

        content_changed(db_session, "model", [model.id])
        db_session.commit()

        assert db_session.exec(visible_passage_ids(db_session, user)).all() == []

    def test_defers_passage_construction_until_background_processing(
        self, projection, db_session, make_model
    ):
        model = make_model("Bracket")

        content_changed(db_session, "model", [model.id])
        db_session.commit()

        assert db_session.exec(select(SearchPassage)).all() == []

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

        _drain(db_session)

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

        _drain(db_session)

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

        _drain(db_session)

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

        _drain(db_session)

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

        _drain(db_session)
        assert db_session.exec(select(SearchPassage.text)).one() == "Title: Bracket"

    def test_rolls_back_a_content_notification(
        self, projection, db_session, make_model
    ):
        model = make_model("Before")
        content_changed(db_session, "model", [model.id])
        _drain(db_session)
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


@pytest.fixture
def statements(db_session) -> list[str]:
    """Every statement the application database is sent."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, *_rest) -> None:
        seen.append(statement.split()[0].upper())

    target = db_session.get_bind()
    event.listen(target, "before_cursor_execute", record)
    yield seen
    event.remove(target, "before_cursor_execute", record)


class TestPreparePending:
    def test_reads_the_page_without_writing(
        self, db_session, make_model, make_search_projection_request, statements
    ):
        from app.db.projections import ContentSource

        model = make_model("Bracket")
        make_search_projection_request(ContentSource("model", model.id))
        statements.clear()

        page = prepare_pending(db_session)

        assert (
            [subject.subject_id for subject, _ in page.subjects],
            set(statements),
        ) == (
            [model.id],
            {"SELECT", "WITH"},
        )

    def test_carries_what_each_subject_projects_to(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        model = make_model("Bracket")
        make_search_projection_request(ContentSource("model", model.id))

        page = prepare_pending(db_session)

        ((_subject, prepared),) = page.subjects
        assert prepared.projection.segments[0].content.title == "Bracket"

    def test_a_trashed_subject_projects_to_nothing(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        model = make_model("Bracket", trashed=True)
        make_search_projection_request(ContentSource("model", model.id))

        page = prepare_pending(db_session)

        ((_subject, prepared),) = page.subjects
        assert (prepared.projection, prepared.dependencies) == (None, frozenset())

    def test_nothing_due_is_no_page(self, db_session):
        assert prepare_pending(db_session) is None

    def test_a_delayed_request_is_not_due(
        self, db_session, make_model, make_search_projection_request
    ):
        from datetime import timedelta

        from app.core.time import utcnow
        from app.db.projections import ContentSource

        request = make_search_projection_request(
            ContentSource("model", make_model().id)
        )
        request.next_attempt_at = utcnow() + timedelta(minutes=5)
        db_session.add(request)
        db_session.commit()

        assert prepare_pending(db_session) is None

    @pytest.mark.parametrize("limit", [0, 129], ids=["zero", "over-page"])
    def test_rejects_an_unbounded_page(self, db_session, limit):
        with pytest.raises(ValueError, match="search_projection_limit"):
            prepare_pending(db_session, limit=limit)


class TestApplyPrepared:
    def test_writes_the_prepared_page(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        make_search_projection_request(ContentSource("model", make_model("Bracket").id))
        page = prepare_pending(db_session)
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert (written, db_session.exec(select(SearchPassage.text)).all()) == (
            1,
            ["Title: Bracket"],
        )

    def test_finishes_a_request_whose_last_page_it_wrote(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        make_search_projection_request(ContentSource("model", make_model().id))
        page = prepare_pending(db_session)
        db_session.commit()

        apply_prepared(db_session, page)
        db_session.commit()

        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_reads_no_library_content_under_the_writer(
        self, db_session, make_model, make_search_projection_request, statements
    ):
        from app.db.projections import ContentSource

        make_search_projection_request(ContentSource("model", make_model().id))
        page = prepare_pending(db_session)
        db_session.commit()
        statements.clear()

        apply_prepared(db_session, page)

        # The recursive tag read is the content projection's signature.
        assert "WITH" not in statements

    def test_a_request_changed_since_the_page_was_read_is_left_due(
        self, projection, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        model = make_model("Before")
        make_search_projection_request(ContentSource("model", model.id))
        page = prepare_pending(db_session)
        db_session.commit()
        model.name = "After"
        db_session.add(model)
        content_changed(db_session, "model", [model.id])
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert (
            written,
            db_session.exec(select(SearchPassage)).all(),
            db_session.exec(select(SearchProjectionRequest.source_id)).all(),
        ) == (None, [], [model.id])

    def test_a_page_another_worker_already_wrote_is_not_written_twice(
        self, db_session, make_collection, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        collection = make_collection("Collection")
        make_model("Bracket", collection=collection)
        make_search_projection_request(ContentSource("collection", collection.id))
        page = prepare_pending(db_session, limit=1)
        db_session.commit()
        process_pending(db_session, limit=1)
        db_session.commit()

        written = apply_prepared(db_session, page)

        assert written is None

    def test_a_request_that_is_gone_is_not_written(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        make_search_projection_request(ContentSource("model", make_model().id))
        page = prepare_pending(db_session)
        db_session.exec(delete(SearchProjectionRequest))
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert (written, db_session.exec(select(SearchPassage)).all()) == (None, [])

    def test_a_change_made_after_the_read_is_projected_by_its_own_request(
        self, projection, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        collection_model = make_model("Before")
        other = make_model("Unrelated")
        make_search_projection_request(ContentSource("model", other.id))
        page = prepare_pending(db_session)
        db_session.commit()
        collection_model.name = "After"
        db_session.add(collection_model)
        content_changed(db_session, "model", [collection_model.id])
        db_session.commit()
        apply_prepared(db_session, page)

        _drain(db_session)

        assert sorted(db_session.exec(select(SearchPassage.text)).all()) == [
            "Title: After",
            "Title: Unrelated",
        ]


class TestProcessPending:
    def test_retries_a_failed_page_atomically(
        self, db_session, make_model, make_search_projection_request, monkeypatch
    ):
        from datetime import timedelta

        from app.core.time import utcnow
        from app.db.projections import ContentSource
        from app.modules.search import projection

        model = make_model("Retained source")
        make_search_projection_request(ContentSource("model", model.id))
        original = projection.sync_subject

        def fail_after_write(session, subject, **prepared):
            original(session, subject, **prepared)
            session.flush()
            raise RuntimeError("projection unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(projection, "sync_subject", fail_after_write)
            assert process_pending(db_session) == 0
            db_session.commit()

        assert db_session.exec(select(SearchPassage)).all() == []
        request = db_session.exec(select(SearchProjectionRequest)).one()
        assert (request.attempts, request.error_code, request.cursor_id) == (
            1,
            "projection_failed",
            0,
        )
        assert request.next_attempt_at.replace(tzinfo=None) > utcnow().replace(
            tzinfo=None
        )
        assert model.name == "Retained source"
        request.next_attempt_at = utcnow() - timedelta(seconds=1)
        db_session.add(request)
        db_session.commit()
        assert process_pending(db_session) == 1
        db_session.commit()
        assert (
            db_session.exec(select(SearchPassage.text)).one()
            == "Title: Retained source"
        )
        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_resumes_a_committed_request_in_a_fresh_session(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource
        from app.db.session import get_session_factory

        model = make_model("Bracket")
        make_search_projection_request(ContentSource("model", model.id))

        with get_session_factory().scoped_session() as worker:
            process_pending(worker)
            worker.commit()

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: Bracket"]
        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_limits_collection_fanout_to_one_page(
        self, db_session, make_collection, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        collection = make_collection("Collection")
        make_model("Bracket", collection=collection)
        make_search_projection_request(ContentSource("collection", collection.id))

        process_pending(db_session, limit=1)
        db_session.commit()

        assert db_session.exec(select(SearchPassage.subject_type)).all() == [
            "collection"
        ]
        row = db_session.exec(select(SearchProjectionRequest)).one()
        assert (row.cursor_kind, row.cursor_id) == ("collection", collection.id)

    def test_keeps_work_when_a_page_transaction_rolls_back(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        model = make_model("Bracket")
        make_search_projection_request(ContentSource("model", model.id))

        process_pending(db_session)
        db_session.rollback()

        assert db_session.exec(select(SearchPassage)).all() == []
        assert db_session.exec(select(SearchProjectionRequest.source_id)).all() == [
            model.id
        ]

    @pytest.mark.parametrize("limit", [0, 129], ids=["zero", "over-page"])
    def test_rejects_an_unbounded_page(self, db_session, limit):
        with pytest.raises(ValueError, match="search_projection_limit"):
            process_pending(db_session, limit=limit)
