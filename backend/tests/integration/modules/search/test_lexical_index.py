"""Canonical lexical predicates retain current recipe membership across reads."""

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import select

from app.db.models import SearchPassage
from app.modules.search.lexical_index import canonical_passage


def test_reads_a_new_recipe_after_reusing_the_canonical_predicate(
    db_session, make_model, make_search_passage
):
    model = make_model("Bracket")
    subject = SearchSubject(SubjectType.MODEL, model.id)
    first = make_search_passage(subject, recipe_version=1)
    statement = select(SearchPassage.id).where(canonical_passage())
    assert db_session.exec(statement).all() == [first.id]

    second = make_search_passage(subject, recipe_version=2)

    assert db_session.exec(statement).all() == [second.id]
