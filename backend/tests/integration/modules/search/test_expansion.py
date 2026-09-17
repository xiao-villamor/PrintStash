"""Sparse retrieval preserves original text, access filtering and toggle semantics."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import delete, union_all
from sqlmodel import select

from app.db.models import SearchExpansion, SearchExpansionTerm, SearchPassage
from app.modules.search import configuration, expansion
from app.modules.search.lexical_query import ordered_passages
from app.modules.search.passages import sync_subject
from app.modules.search.retrieval import search


class TestExpansion:
    def test_repeated_query_changes_keep_the_authorized_scope(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_expansion,
        make_search_expansion_term,
    ):
        from app.modules.search.access import visible_passage_ids

        actor, recipe = sparse_setup
        bicycle = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, bicycle.id))
        passage = db_session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_type == "model",
                SearchPassage.subject_id == bicycle.id,
            )
        ).one()
        row = make_search_expansion(passage, recipe=recipe.id)
        make_search_expansion_term(row, "bike")
        excluded = make_model("bike")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, excluded.id))
        excluded_passage = db_session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_type == "model",
                SearchPassage.subject_id == excluded.id,
            )
        ).one()
        excluded_row = make_search_expansion(excluded_passage, recipe=recipe.id)
        make_search_expansion_term(excluded_row, "bike")

        for query in ("bike", "bicycle", "unmatchedword") * 12:
            allowed = visible_passage_ids(db_session, actor).where(
                SearchPassage.id == passage.id
            )
            ranks = db_session.exec(ordered_passages(db_session, query, allowed)).all()
            assert [item[0] for item in ranks] == (
                [] if query == "unmatchedword" else [passage.id]
            )

        readers = [
            ordered_passages(db_session, query, allowed).subquery()
            for query in ("bike", "bicycle")
        ]
        combined = union_all(*(select(reader.c.passage_id) for reader in readers))
        assert [item[0] for item in db_session.exec(combined).all()] == [
            passage.id,
            passage.id,
        ]

    @pytest.mark.parametrize("like", [False, True])
    def test_ranks_separate_expansion_terms(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_expansion,
        make_search_expansion_term,
        like,
    ):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        passage = db_session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_id == subject.id,
                SearchPassage.subject_type == "model",
            )
        ).one()
        row = make_search_expansion(passage, recipe=model.id)
        make_search_expansion_term(row, weight=1.6)
        ranks = db_session.exec(
            ordered_passages(
                db_session, "bike", select(SearchPassage.id), force_like=like
            )
        ).all()
        assert ranks[0][0] == passage.id
        assert 0 < ranks[0][1] <= expansion.CONTRIBUTION_CAP / 61

    def test_removes_contribution_immediately_on_disable(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_expansion,
        make_search_expansion_term,
    ):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        passage = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_type == "model")
        ).one()
        row = make_search_expansion(passage, recipe=model.id)
        make_search_expansion_term(row)
        assert search(db_session, actor, "bike").items
        config = configuration.settings(db_session).model_copy(
            update={"sparse_expansion_enabled": False}
        )
        configuration.update(db_session, config)
        db_session.commit()
        assert search(db_session, actor, "bike").items == []
        assert [
            item.subject_id for item in search(db_session, actor, "bicycle").items
        ] == [subject.id]
        assert db_session.get(SearchExpansion, passage.id) is not None

    @pytest.mark.parametrize("invalid", ["input_hash", "recipe", "phase"])
    def test_ignores_ineligible_expansion_rows(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_passage,
        make_search_expansion,
        make_search_expansion_term,
        invalid,
    ):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, subject.id))
        row = make_search_expansion(passage, recipe=model.id)
        setattr(row, invalid, "failed" if invalid == "phase" else "f" * 64)
        db_session.add(row)
        make_search_expansion_term(row)
        assert search(db_session, actor, "bike").items == []

    def test_filters_sparse_candidates_before_ranking(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_passage,
        make_search_expansion,
        make_search_expansion_term,
    ):
        _, model = sparse_setup
        subject = make_model("bicycle")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, subject.id))
        row = make_search_expansion(passage, recipe=model.id)
        make_search_expansion_term(row)
        allowed = select(SearchPassage.id).where(SearchPassage.id != passage.id)
        assert (
            db_session.exec(ordered_passages(db_session, "bike", allowed)).all() == []
        )

    def test_cascades_sparse_rows_when_a_passage_is_purged(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_passage,
        make_search_expansion,
        make_search_expansion_term,
    ):
        _, model = sparse_setup
        subject = make_model("bicycle")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, subject.id))
        row = make_search_expansion(passage, recipe=model.id)
        make_search_expansion_term(row)
        db_session.exec(delete(SearchPassage).where(SearchPassage.id == passage.id))
        db_session.commit()
        assert db_session.exec(select(SearchExpansion)).all() == []
        assert db_session.exec(select(SearchExpansionTerm)).all() == []

    def test_preserves_original_passage_evidence(
        self,
        db_session,
        sparse_setup,
        make_model,
        make_search_expansion,
        make_search_expansion_term,
    ):
        actor, model = sparse_setup
        subject = make_model("bicycle")
        sync_subject(db_session, SearchSubject(SubjectType.MODEL, subject.id))
        passage = db_session.exec(
            select(SearchPassage).where(SearchPassage.subject_type == "model")
        ).one()
        original = passage.text
        row = make_search_expansion(passage, recipe=model.id)
        make_search_expansion_term(row)
        results = search(db_session, actor, "bike").items
        assert results[0].name == "bicycle"
        assert results[0].evidence
        assert all("bike" not in evidence.text for evidence in results[0].evidence)
        db_session.refresh(passage)
        assert passage.text == original
