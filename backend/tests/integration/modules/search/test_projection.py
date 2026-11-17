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

    def test_loads_model_contributors_in_a_fixed_number_of_queries(
        self,
        db_session,
        make_collection,
        make_model,
        make_file,
        make_provenance_source,
        make_tag,
        tag_collection,
        make_search_projection_request,
        statements,
    ):
        from app.db.projections import ContentSource

        root = make_collection("Root")
        child = make_collection("Child", parent=root)
        tag_collection(root, make_tag("Inherited"))
        models = [make_model(f"Model {i}", collection=child) for i in range(20)]
        for model in models:
            make_file(model)
            make_provenance_source(model, tags=["Imported"])
        make_search_projection_request(ContentSource("collection", root.id))
        statements.clear()

        small = prepare_pending(db_session, limit=4)
        small_reads = len(statements)
        statements.clear()
        large = prepare_pending(db_session, limit=32)

        assert len(small.subjects) == 4
        assert len(large.subjects) == 22
        assert len(statements) <= small_reads

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

    def test_replays_unchanged_lexical_content_without_writes(
        self, projection, db_session, make_model, statements
    ):
        from sqlalchemy import text

        from app.modules.search.lexical_index import rebuild_partition

        model = make_model("Bracket")
        rebuild_partition(db_session)
        content_changed(db_session, "model", [model.id])
        _drain(db_session)
        content_changed(db_session, "model", [model.id])
        db_session.commit()
        page = prepare_pending(db_session)
        db_session.commit()
        statements.clear()

        apply_prepared(db_session, page)
        writes = statements.count("INSERT")

        assert writes == 0
        assert (
            db_session.execute(
                text(
                    "SELECT rowid FROM search_passages_fts WHERE search_passages_fts MATCH 'bracket'"
                )
            )
            .scalars()
            .all()
            == db_session.exec(select(SearchPassage.id)).all()
        )

    def test_replaces_lexical_membership_when_a_page_promotes_a_recipe(
        self, projection, db_session, make_model, make_system_config
    ):
        from app.db.models.search import SearchLexicalPosting, SearchLexicalState
        from app.schemas.inference import SearchSettings

        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        _drain(db_session)
        make_system_config(
            ai_search_settings_json=SearchSettings(
                captions_enabled=True
            ).model_dump_json()
        )

        content_changed(db_session, "model", [model.id])
        _drain(db_session)

        versions = db_session.exec(
            select(SearchPassage.recipe_version, SearchPassage.token_count).order_by(
                SearchPassage.recipe_version
            )
        ).all()
        assert versions[0] == (1, 0)
        assert versions[1][0] == 2
        assert versions[1][1] > 0
        assert db_session.get(SearchLexicalState, 1).document_count == 1
        assert db_session.exec(
            select(SearchLexicalPosting.term).where(
                SearchLexicalPosting.term == "bracket"
            )
        ).all() == ["bracket"]

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

    def test_applies_overlapping_sources_once(
        self, db_session, make_collection, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        collection = make_collection("Collection")
        model = make_model("Bracket", collection=collection)
        make_search_projection_request(ContentSource("collection", collection.id))
        make_search_projection_request(ContentSource("model", model.id))
        page = prepare_pending(db_session)
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert written == 2
        assert len(db_session.exec(select(SearchPassage)).all()) == 2
        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_bounds_distinct_subjects_across_requests(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        models = [make_model(f"Part {index}") for index in range(3)]
        requests = [
            make_search_projection_request(ContentSource("model", model.id))
            for model in models
        ]
        page = prepare_pending(db_session, limit=2)
        db_session.commit()

        apply_prepared(db_session, page)
        db_session.commit()

        assert len(db_session.exec(select(SearchPassage)).all()) == 2
        assert db_session.exec(select(SearchProjectionRequest.id)).all() == [
            requests[2].id
        ]

    def test_keeps_uncovered_source_work_due(
        self, db_session, make_collection, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        collection = make_collection("Collection")
        first = make_model("First", collection=collection)
        second = make_model("Second", collection=collection)
        source = make_search_projection_request(
            ContentSource("collection", collection.id)
        )
        other = make_search_projection_request(ContentSource("model", second.id))
        page = prepare_pending(db_session, limit=2)
        db_session.commit()

        apply_prepared(db_session, page)
        db_session.commit()

        db_session.refresh(source)
        assert (source.cursor_kind, source.cursor_id) == ("model", first.id)
        assert db_session.get(SearchProjectionRequest, other.id) is not None
        _drain(db_session)
        assert len(db_session.exec(select(SearchPassage)).all()) == 3

    def test_rejects_a_batch_with_a_changed_later_request(
        self, projection, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        first = make_model("First")
        second = make_model("Before")
        make_search_projection_request(ContentSource("model", first.id))
        make_search_projection_request(ContentSource("model", second.id))
        page = prepare_pending(db_session)
        db_session.commit()
        second.name = "After"
        db_session.add(second)
        content_changed(db_session, "model", [second.id])
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert written is None
        assert db_session.exec(select(SearchPassage)).all() == []
        _drain(db_session)
        assert sorted(db_session.exec(select(SearchPassage.text)).all()) == [
            "Title: After",
            "Title: First",
        ]

    def test_rejects_a_batch_with_a_deleted_later_request(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        make_search_projection_request(ContentSource("model", make_model("First").id))
        other = make_search_projection_request(
            ContentSource("model", make_model("Second").id)
        )
        page = prepare_pending(db_session)
        db_session.commit()
        db_session.delete(other)
        db_session.commit()

        written = apply_prepared(db_session, page)
        db_session.commit()

        assert written is None
        assert db_session.exec(select(SearchPassage)).all() == []

    def test_rolls_back_all_source_progress(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        models = [make_model(f"Part {index}") for index in range(2)]
        requests = [
            make_search_projection_request(ContentSource("model", model.id))
            for model in models
        ]
        page = prepare_pending(db_session)
        db_session.commit()

        apply_prepared(db_session, page)
        db_session.rollback()

        assert db_session.exec(select(SearchPassage)).all() == []
        assert set(db_session.exec(select(SearchProjectionRequest.id)).all()) == {
            request.id for request in requests
        }

    def test_a_failed_source_leaves_other_sources_progressing(
        self, db_session, make_model, make_search_projection_request, monkeypatch
    ):
        from app.db.projections import ContentSource
        from app.modules.search import projection

        broken = make_model("Broken")
        good = make_model("Good")
        request = make_search_projection_request(ContentSource("model", broken.id))
        make_search_projection_request(ContentSource("model", good.id))
        page = prepare_pending(db_session)
        db_session.commit()
        original = projection.sync_subject

        def fail_one(session, subject, **prepared):
            result = original(session, subject, **prepared)
            if subject.subject_id == broken.id:
                raise RuntimeError("projection unavailable")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(projection, "sync_subject", fail_one)
            apply_prepared(db_session, page)
            db_session.commit()

        assert db_session.exec(select(SearchPassage.text)).all() == ["Title: Good"]
        retained = db_session.exec(select(SearchProjectionRequest)).one()
        assert (retained.id, retained.attempts, retained.error_code) == (
            request.id,
            1,
            "projection_failed",
        )

    def test_bounds_the_source_batch(
        self, db_session, make_model, make_search_projection_request
    ):
        from app.db.projections import ContentSource

        models = [make_model(f"Part {index}") for index in range(12)]
        requests = [
            make_search_projection_request(ContentSource("model", model.id))
            for model in models
        ]
        page = prepare_pending(db_session, limit=128)
        db_session.commit()

        apply_prepared(db_session, page)
        db_session.commit()

        assert len(db_session.exec(select(SearchPassage)).all()) == 8
        assert (
            len(db_session.exec(select(SearchProjectionRequest)).all())
            == len(requests) - 8
        )

    @pytest.mark.parametrize("collection_first", [True, False])
    def test_shared_subject_survives_an_overlapping_source_failure(
        self,
        db_session,
        make_collection,
        make_model,
        make_search_projection_request,
        monkeypatch,
        collection_first,
    ):
        from app.db.projections import ContentSource
        from app.modules.search import projection

        collection = make_collection("Collection")
        shared = make_model("Shared", collection=collection)
        broken = make_model("Broken", collection=collection)
        sources = [
            ContentSource("collection", collection.id),
            ContentSource("model", shared.id),
        ]
        for source in sources if collection_first else reversed(sources):
            make_search_projection_request(source)
        page = prepare_pending(db_session)
        db_session.commit()
        original = projection.sync_subject

        def fail_late(session, subject, **prepared):
            result = original(session, subject, **prepared)
            if (
                subject.subject_type.value == "model"
                and subject.subject_id == broken.id
            ):
                raise RuntimeError("projection unavailable")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(projection, "sync_subject", fail_late)
            apply_prepared(db_session, page)
            db_session.commit()

        assert db_session.exec(select(SearchPassage.text)).all() == [
            f"Title: Shared\nCollection: {collection.path}"
        ]
        retained = db_session.exec(select(SearchProjectionRequest)).one()
        assert (retained.source_kind, retained.source_id, retained.attempts) == (
            "collection",
            collection.id,
            1,
        )


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
