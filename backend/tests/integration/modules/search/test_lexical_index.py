"""Lexical writes retain exact BM25 statistics with bounded database dispatch.

Batching an imported passage's vocabulary must retain shared terms, idempotency
and rollback; canonical reads must still follow the current recipe.
"""

from collections.abc import Iterator

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import event
from sqlmodel import Session, select

from app.db.models import (
    SearchLexicalPosting,
    SearchLexicalState,
    SearchLexicalTerm,
    SearchPassage,
)
from app.modules.search import lexical_index
from app.modules.search.lexical_index import canonical_passage


@pytest.fixture
def term_dispatches(db_session: Session) -> Iterator[list[tuple[str, bool, int]]]:
    calls: list[tuple[str, bool, int]] = []

    def record(_conn, _cursor, statement, parameters, _context, executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(
            (
                "insert into search_lexical_terms ",
                "update search_lexical_terms ",
                "delete from search_lexical_terms ",
            )
        ):
            calls.append((normalized.split()[0], executemany, len(parameters)))

    target = db_session.get_bind()
    event.listen(target, "before_cursor_execute", record)
    try:
        yield calls
    finally:
        event.remove(target, "before_cursor_execute", record)


class TestReplace:
    def test_persists_weighted_postings(
        self, db_session, make_model, make_search_passage
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="Bracket",
            tags_text="Blue",
            text="bracket bracket",
        )

        lexical_index.replace(db_session, row, None)
        db_session.commit()

        assert dict(
            db_session.exec(
                select(SearchLexicalPosting.term, SearchLexicalPosting.frequency)
            ).all()
        ) == {"bracket": 7.0, "blue": 3.0}
        assert dict(
            db_session.exec(
                select(SearchLexicalTerm.term, SearchLexicalTerm.document_frequency)
            ).all()
        ) == {"bracket": 1, "blue": 1}
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length, row.token_count) == (1, 4, 4)

    def test_replacement_preserves_shared_vocabulary(
        self, db_session, make_model, make_search_passage
    ):
        first = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket old",
            text="",
        )
        second = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket",
            text="",
        )
        lexical_index.replace(db_session, first, None)
        lexical_index.replace(db_session, second, None)
        db_session.commit()
        old = lexical_index.snapshot(first)
        retained = db_session.get(SearchLexicalTerm, "bracket")
        first.title = "support support"

        lexical_index.replace(db_session, first, old)

        assert retained.document_frequency == 1
        assert db_session.get(SearchLexicalTerm, "old") is None
        assert dict(
            db_session.exec(
                select(SearchLexicalTerm.term, SearchLexicalTerm.document_frequency)
            ).all()
        ) == {"bracket": 1, "support": 1}
        assert dict(
            db_session.exec(
                select(SearchLexicalPosting.term, SearchLexicalPosting.frequency).where(
                    SearchLexicalPosting.passage_id == first.id
                )
            ).all()
        ) == {"support": 10.0}
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (2, 3)

    def test_replacement_keeps_existing_terms_at_one_document(
        self, db_session, make_model, make_search_passage
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket",
            text="",
        )
        lexical_index.replace(db_session, row, None)
        db_session.commit()
        old = lexical_index.snapshot(row)
        row.title = "bracket bracket"

        lexical_index.replace(db_session, row, old)
        db_session.commit()

        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1
        posting = db_session.exec(select(SearchLexicalPosting)).one()
        assert (posting.term, posting.frequency) == ("bracket", 10.0)
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (1, 2)

    def test_repeated_replacement_leaves_statistics_unchanged(
        self, db_session, make_model, make_search_passage, term_dispatches
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket",
            text="",
        )
        lexical_index.replace(db_session, row, None)
        db_session.commit()
        old = lexical_index.snapshot(row)
        term_dispatches.clear()

        lexical_index.replace(db_session, row, old)
        db_session.commit()

        assert term_dispatches == []
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1
        posting = db_session.exec(select(SearchLexicalPosting)).one()
        assert (posting.term, posting.frequency) == ("bracket", 5.0)
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (1, 1)

    @pytest.mark.parametrize(
        ("deleted", "replacement_title"),
        [
            pytest.param(False, "", id="empty-content"),
            pytest.param(True, "still searchable", id="deleted"),
        ],
    )
    def test_removal_clears_passage_statistics(
        self, db_session, make_model, make_search_passage, deleted, replacement_title
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket",
            text="",
        )
        lexical_index.replace(db_session, row, None)
        db_session.commit()
        old = lexical_index.snapshot(row)
        row.title = replacement_title

        lexical_index.replace(db_session, row, old, deleted=deleted)
        db_session.commit()

        assert db_session.exec(select(SearchLexicalTerm)).all() == []
        assert db_session.exec(select(SearchLexicalPosting)).all() == []
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length, row.token_count) == (0, 0, 0)

    @pytest.mark.parametrize(
        ("term_count", "dispatch_count"),
        [
            pytest.param(1, 1, id="one"),
            pytest.param(256, 1, id="full-batch"),
            pytest.param(257, 2, id="one-over-batch"),
            pytest.param(600, 3, id="several-batches"),
        ],
    )
    def test_addition_bounds_database_dispatches(
        self,
        db_session,
        make_model,
        make_search_passage,
        term_dispatches,
        term_count,
        dispatch_count,
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="",
            text=" ".join(f"term{i}" for i in range(term_count)),
        )
        term_dispatches.clear()

        lexical_index.replace(db_session, row, None)
        db_session.commit()

        assert len(term_dispatches) == dispatch_count
        assert {operation for operation, _, _ in term_dispatches} == {"insert"}
        assert max(size for _, _, size in term_dispatches) <= 256
        assert (
            sum(size if many else 1 for _, many, size in term_dispatches) == term_count
        )
        assert dict(
            db_session.exec(
                select(SearchLexicalTerm.term, SearchLexicalTerm.document_frequency)
            ).all()
        ) == {f"term{i}": 1 for i in range(term_count)}
        assert dict(
            db_session.exec(
                select(SearchLexicalPosting.term, SearchLexicalPosting.frequency)
            ).all()
        ) == {f"term{i}": 1.0 for i in range(term_count)}
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (1, term_count)

    @pytest.mark.parametrize(
        ("term_count", "dispatch_count"),
        [
            pytest.param(1, 2, id="one"),
            pytest.param(256, 2, id="full-batch"),
            pytest.param(257, 4, id="one-over-batch"),
            pytest.param(600, 6, id="several-batches"),
        ],
    )
    def test_removal_bounds_database_dispatches(
        self,
        db_session,
        make_model,
        make_search_passage,
        term_dispatches,
        term_count,
        dispatch_count,
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="",
            text=" ".join(f"term{i}" for i in range(term_count)),
        )
        lexical_index.replace(db_session, row, None)
        db_session.commit()
        old = lexical_index.snapshot(row)
        term_dispatches.clear()

        lexical_index.replace(db_session, row, old, deleted=True)
        db_session.commit()

        assert len(term_dispatches) == dispatch_count
        assert {operation for operation, _, _ in term_dispatches} == {
            "update",
            "delete",
        }
        assert {many for _, many, _ in term_dispatches} == {False}
        assert max(size for _, _, size in term_dispatches) <= 257
        assert db_session.exec(select(SearchLexicalTerm)).all() == []
        assert db_session.exec(select(SearchLexicalPosting)).all() == []
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (0, 0)

    def test_rollback_restores_the_published_statistics(
        self, db_session, make_model, make_search_passage
    ):
        row = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model().id),
            title="bracket old",
            text="",
        )
        lexical_index.replace(db_session, row, None)
        db_session.commit()
        old = lexical_index.snapshot(row)
        row.title = "support new"

        lexical_index.replace(db_session, row, old)
        db_session.rollback()

        assert row.title == "bracket old"
        assert dict(
            db_session.exec(
                select(SearchLexicalTerm.term, SearchLexicalTerm.document_frequency)
            ).all()
        ) == {"bracket": 1, "old": 1}
        assert dict(
            db_session.exec(
                select(SearchLexicalPosting.term, SearchLexicalPosting.frequency)
            ).all()
        ) == {"bracket": 5.0, "old": 5.0}
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length, row.token_count) == (1, 2, 2)


class TestCanonicalPassage:
    def test_reads_a_new_recipe_after_reusing_the_canonical_predicate(
        self, db_session, make_model, make_search_passage
    ):
        model = make_model("Bracket")
        subject = SearchSubject(SubjectType.MODEL, model.id)
        first = make_search_passage(subject, recipe_version=1)
        statement = select(SearchPassage.id).where(canonical_passage())
        assert db_session.exec(statement).all() == [first.id]

        second = make_search_passage(subject, recipe_version=2)

        assert db_session.exec(statement).all() == [second.id]
