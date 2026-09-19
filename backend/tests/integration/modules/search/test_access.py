"""Candidate visibility stays fresh without scanning unrelated library owners."""

import json

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType

from app.core.time import utcnow
from app.db.models import SearchPassage
from app.modules.search.access import visible_passage_ids
from tests.factories import grant_collection_role
from tests.fakes.sqlite_work import sqlite_work


class TestVisiblePassageIds:
    @pytest.mark.parametrize("contributor_state", ["visible", "hidden", "trashed"])
    def test_bounds_visibility_work_to_candidate_identities(
        self,
        db_session,
        make_user,
        make_collection,
        make_model,
        make_search_passage,
        contributor_state,
    ):
        actor = make_user()
        owned = make_collection("Visible")
        hidden = make_collection("Hidden")
        grant_collection_role(db_session, actor, owned)
        target = make_model("Target", collection_id=owned.id)
        contributor = make_model(
            "Contributor",
            collection_id=hidden.id if contributor_state == "hidden" else owned.id,
            deleted_at=utcnow() if contributor_state == "trashed" else None,
        )
        passage = make_search_passage(
            SearchSubject(SubjectType.MODEL, target.id),
            access_dependencies_json=json.dumps([["model", contributor.id]]),
        )
        db_session.commit()
        statement = visible_passage_ids(db_session, actor).where(
            SearchPassage.id == passage.id
        )
        with sqlite_work(db_session) as small:
            before = db_session.execute(statement).scalars().all()
        for index in range(1000):
            make_model(f"Unrelated {index}", collection_id=owned.id)
        db_session.commit()
        statement = visible_passage_ids(db_session, actor).where(
            SearchPassage.id == passage.id
        )
        with sqlite_work(db_session) as large:
            after = db_session.execute(statement).scalars().all()
        assert (
            before == after == ([passage.id] if contributor_state == "visible" else [])
        )
        assert large.instructions <= max(1000, small.instructions * 2)


class TestPassageInScope:
    @pytest.mark.parametrize("shape", ["limited", "joined", "distinct", "empty"])
    def test_preserves_bounded_scope_membership(
        self, db_session, make_model, make_search_passage, shape
    ):
        from sqlmodel import select

        from app.db.models import Model
        from app.modules.search.access import passage_in_scope

        first = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model("First").id)
        )
        second = make_search_passage(
            SearchSubject(SubjectType.MODEL, make_model("Second").id)
        )
        scope = select(SearchPassage.id)
        if shape == "limited":
            scope = scope.order_by(SearchPassage.id.desc()).limit(1)
        elif shape == "joined":
            scope = scope.join(Model, Model.id == SearchPassage.subject_id).where(
                Model.name == "Second"
            )
        elif shape == "distinct":
            scope = scope.where(SearchPassage.id == first.id).distinct()
        else:
            scope = scope.where(SearchPassage.id == -1)
        expected = db_session.exec(scope).all()
        found = db_session.exec(
            select(SearchPassage.id).where(passage_in_scope(scope))
        ).all()
        assert found == expected
        assert found == (
            []
            if shape == "empty"
            else [first.id]
            if shape == "distinct"
            else [second.id]
        )
