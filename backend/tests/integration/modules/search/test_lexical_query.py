"""Native lexical results retain exact transactional content and authorization."""

import pytest
from sqlalchemy import text

from app.db.projections import bind_content_projection, content_changed
from app.modules.library.commands import update_model
from app.modules.library.trash import soft_delete_model
from app.modules.search import lexical_index
from app.modules.search.projection import LibraryProjection
from app.modules.search.retrieval import search
from app.schemas.models import ModelUpdate


@pytest.fixture(autouse=True)
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestLexicalQuery:
    def test_maintains_statistics_through_the_content_lifecycle(
        self, db_session, make_user, make_model
    ):
        from app.db.models import SearchLexicalState, SearchLexicalTerm

        actor = make_user(superuser=True)
        first, second = make_model("Bracket"), make_model("Bracket")
        content_changed(db_session, "model", [first.id, second.id])
        db_session.commit()
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (2, 6)
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 2

        update_model(first.id, ModelUpdate(name="Support"), actor, db_session)
        db_session.expire_all()
        assert (state.document_count, state.total_length) == (2, 6)
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1
        assert db_session.get(SearchLexicalTerm, "support").document_frequency == 1

        soft_delete_model(db_session, first)
        db_session.expire_all()
        assert (state.document_count, state.total_length) == (1, 3)
        assert db_session.get(SearchLexicalTerm, "support") is None
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1

    def test_ranks_exact_title_above_body(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        body = make_model("Notes", description="bracket")
        title = make_model("Bracket")
        content_changed(db_session, "model", [body.id, title.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "fts5"
        assert [row.subject_id for row in result.items] == [title.id, body.id]

    def test_searches_current_content_after_edit(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        update_model(model.id, ModelUpdate(name="Hinge"), actor, db_session)

        assert [row.subject_id for row in search(db_session, actor, "hinge").items] == [
            model.id
        ]
        assert search(db_session, actor, "bracket").items == []

    def test_removes_deleted_content_from_native_index(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        soft_delete_model(db_session, model)

        assert search(db_session, actor, "bracket").items == []

    def test_rolls_back_lexical_edits(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        model.name = "Hinge"
        db_session.add(model)
        content_changed(db_session, "model", [model.id])
        db_session.rollback()

        assert [
            row.subject_id for row in search(db_session, actor, "bracket").items
        ] == [model.id]
        assert search(db_session, actor, "hinge").items == []

    def test_falls_back_when_fts_is_unavailable(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        db_session.commit()

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "ranked_like"
        assert [row.subject_id for row in result.items] == [model.id]

    def test_preserves_content_when_native_update_fails(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        db_session.execute(text("DROP TABLE search_passages_fts"))
        db_session.commit()

        update_model(model.id, ModelUpdate(name="Hinge"), actor, db_session)
        result = search(db_session, actor, "hinge")

        assert result.lexical_backend == "ranked_like"
        assert [row.subject_id for row in result.items] == [model.id]

    def test_repairs_native_index_in_bounded_pages(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        models = [make_model(f"Bracket {i}") for i in range(3)]
        content_changed(db_session, "model", [row.id for row in models])
        assert lexical_index.rebuild_partition(db_session, limit=2) == 2
        db_session.commit()
        assert lexical_index.capability(db_session) == "ranked_like"
        assert lexical_index.rebuild_partition(db_session, limit=2) == 1
        db_session.commit()

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "fts5"
        assert {row.subject_id for row in result.items} == {row.id for row in models}

    def test_escapes_like_wildcards(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        literal = make_model("50%_bracket")
        other = make_model("50mm bracket")
        content_changed(db_session, "model", [literal.id, other.id])
        db_session.commit()

        result = search(db_session, actor, "%_")

        assert [row.subject_id for row in result.items] == [literal.id]

    def test_ranks_library_browse_through_read_port(
        self, db_session, make_user, make_model
    ):
        from app.db.content_search import bind_content_search
        from app.modules.library.model_views.listing import list_items
        from app.modules.search.lexical_query import LibrarySearch

        actor = make_user(superuser=True)
        title = make_model("Bracket")
        body = make_model("Notes", description="bracket")
        content_changed(db_session, "model", [title.id, body.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        previous = bind_content_search(LibrarySearch())
        try:
            rows = list_items(db_session, actor, q="bracket")
            assert [row.id for row in rows] == [title.id, body.id]
        finally:
            bind_content_search(previous)

    def test_paginates_ranked_library_browse(self, db_session, make_user, make_model):
        from app.db.content_search import bind_content_search
        from app.modules.library.model_views.pagination import page_items
        from app.modules.search.lexical_query import LibrarySearch
        from app.schemas.models import ModelFilters

        actor = make_user(superuser=True)
        title = make_model("Bracket")
        body = make_model("Notes", description="bracket")
        content_changed(db_session, "model", [title.id, body.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        previous = bind_content_search(LibrarySearch())
        try:
            first = page_items(
                db_session, actor, filters=ModelFilters(q="bracket"), limit=1
            )
            second = page_items(
                db_session,
                actor,
                filters=ModelFilters(q="bracket"),
                limit=1,
                cursor=first.next_cursor,
            )
            assert [row.id for row in first.items + second.items] == [title.id, body.id]
            assert first.total == second.total == 2
            assert second.next_cursor is None
        finally:
            bind_content_search(previous)

    def test_browse_falls_back_after_native_table_loss(
        self, db_session, make_user, make_model
    ):
        from app.db.content_search import bind_content_search
        from app.modules.library.model_views.listing import list_items
        from app.modules.search.lexical_query import LibrarySearch

        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        db_session.execute(text("DROP TABLE search_passages_fts"))
        db_session.commit()
        previous = bind_content_search(LibrarySearch())
        try:
            assert [row.id for row in list_items(db_session, actor, q="bracket")] == [
                model.id
            ]
        finally:
            bind_content_search(previous)


class TestBoundedCandidates:
    def test_bounds_rare_lookup_work_to_matching_passages(
        self,
        db_session,
        make_user,
        make_model,
        make_search_passage,
    ):
        from printstash_core.search.passages import SearchSubject, SubjectType

        from app.modules.search.access import visible_passage_ids
        from app.modules.search.lexical_query import ordered_passages
        from app.modules.search.passages import sync_subject
        from tests.fakes.sqlite_work import sqlite_work

        actor = make_user(superuser=True)
        target = make_model("UniqueNeedle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, target.id))
        while lexical_index.rebuild_partition(db_session):
            pass
        db_session.commit()
        statement = ordered_passages(
            db_session, "UniqueNeedle", visible_passage_ids(db_session, actor)
        )
        with sqlite_work(db_session) as small:
            before = db_session.exec(statement).all()
        for _ in range(1000):
            model = make_model("Stored passage")
            make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        state = lexical_index.state(db_session)
        state.native_phase = "broken"
        state.native_after_id = 0
        db_session.add(state)
        while lexical_index.rebuild_partition(db_session):
            pass
        db_session.commit()
        statement = ordered_passages(
            db_session, "UniqueNeedle", visible_passage_ids(db_session, actor)
        )
        with sqlite_work(db_session) as large:
            after = db_session.exec(statement).all()
        assert [row[0] for row in before] == [row[0] for row in after]
        assert len(after) == 1
        assert large.instructions <= max(1000, small.instructions * 2)
