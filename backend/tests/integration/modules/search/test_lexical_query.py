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
from tests.search_projection import drain_search


@pytest.fixture(autouse=True)
def projection():
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


class TestLexicalQuery:
    @pytest.mark.parametrize(
        "query",
        ["specter", "spectre", "spectr", "spectrre", "spectxe"],
        ids=["transpose", "exact", "deletion", "insertion", "substitution"],
    )
    def test_finds_a_named_model_despite_one_spelling_error(
        self, db_session, make_user, make_model, query
    ):
        actor = make_user(superuser=True)
        expected = make_model("Spectre_Option_A")
        other = make_model(
            "Organization", description="A decorative spectrometer stand"
        )
        content_changed(db_session, "model", [expected.id, other.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()

        result = search(db_session, actor, query, mode="lexical")

        assert [row.subject_id for row in result.items] == [expected.id]

    @pytest.mark.parametrize(
        "query,name,description",
        [("specter", "Spectre", ""), ("holder", "Phone base", "A phone cradle")],
        ids=["spelling", "functional-metadata"],
    )
    def test_candidates_respect_access_scope(
        self,
        db_session,
        make_user,
        make_collection,
        make_model,
        query,
        name,
        description,
    ):
        from app.db.models import CollectionRole, ModelStar
        from app.schemas.models import ModelFilters
        from tests.factories import grant_collection_role

        viewer = make_user()
        shared = make_collection("Shared")
        private = make_collection("Private")
        grant_collection_role(db_session, viewer, shared, CollectionRole.VIEW)
        visible = make_model(
            name, description=description, collection=shared, starred=True
        )
        db_session.add(ModelStar(user_id=viewer.id, model_id=visible.id))
        db_session.commit()
        hidden = make_model(
            name + " secret", description=description, collection=private
        )
        deleted = make_model(
            name + " deleted", description=description, collection=shared, trashed=True
        )
        excluded = make_model(
            name + " other", description=description, collection=shared
        )
        content_changed(
            db_session, "model", [visible.id, hidden.id, deleted.id, excluded.id]
        )
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()

        result = search(db_session, viewer, query, filters=ModelFilters(favorites=True))

        assert [row.subject_id for row in result.items] == [visible.id]
        assert hidden.name not in str(result)
        assert deleted.name not in str(result)

    def test_exact_names_precede_spelling_recovery_across_pages(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        fuzzy = make_model("Spectre")
        exact = make_model("Specter")
        content_changed(db_session, "model", [fuzzy.id, exact.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        first = search(db_session, actor, "specter", limit=1)
        second = search(db_session, actor, "specter", limit=1, cursor=first.next_cursor)
        assert [item.subject_id for item in first.items] == [exact.id]
        assert [item.subject_id for item in second.items] == [fuzzy.id]
        assert second.next_cursor is None

    @pytest.mark.parametrize("query", ["boat", "specter model", "spxctar"])
    def test_does_not_broaden_short_multiword_or_distant_names(
        self, db_session, make_user, make_model, query
    ):
        model = make_model("Spectre")
        actor = make_user(superuser=True)
        content_changed(db_session, "model", [model.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        assert search(db_session, actor, query).items == []

    @pytest.mark.parametrize(
        "query,description,distractor",
        [
            (
                "holder",
                "An angled cradle for a mobile phone",
                "A plastic phone figurine",
            ),
            (
                "gear",
                "A toothed wheel transfers rotation",
                "A smooth wheel spins freely",
            ),
            (
                "bolt",
                "A threaded screw fastens the assembly",
                "A recess for a screw head",
            ),
        ],
    )
    def test_finds_functional_metadata_without_a_matching_filename(
        self, db_session, make_user, make_model, query, description, distractor
    ):
        actor = make_user(superuser=True)
        expected = make_model("Export_001", description=description)
        other = make_model("Export_002", description=distractor)
        content_changed(db_session, "model", [expected.id, other.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        assert [item.subject_id for item in search(db_session, actor, query).items] == [
            expected.id
        ]

    @pytest.mark.parametrize("limit", [0, -1, 2049])
    def test_rejects_unbounded_candidate_requests(self, db_session, limit):
        from sqlmodel import select

        from app.db.models import SearchPassage
        from app.modules.search.lexical_query import concept_candidates, name_candidates

        with pytest.raises(ValueError, match="search_candidate_limit"):
            name_candidates(
                db_session, "specter", select(SearchPassage.id), limit=limit
            )
        with pytest.raises(ValueError, match="search_candidate_limit"):
            concept_candidates(
                db_session, "holder", select(SearchPassage.id), limit=limit
            )

    def test_private_vocabulary_cannot_crowd_out_visible_names(
        self, db_session, make_user, make_collection, make_model
    ):
        from app.db.models import CollectionRole
        from app.modules.search.access import visible_passage_ids
        from app.modules.search.lexical_query import name_candidates
        from tests.factories import grant_collection_role

        viewer = make_user()
        shared = make_collection("Visible collection")
        grant_collection_role(db_session, viewer, shared, CollectionRole.VIEW)
        expected = make_model("Spectre", collection=shared)
        hidden = make_model("Apples", collection=make_collection("Private"))
        content_changed(db_session, "model", [expected.id, hidden.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        matches = name_candidates(
            db_session, "specter", visible_passage_ids(db_session, viewer), limit=1
        )
        assert len(matches) == 1

    def test_maintains_statistics_through_the_content_lifecycle(
        self, db_session, make_user, make_model
    ):
        from app.db.models import SearchLexicalState, SearchLexicalTerm

        actor = make_user(superuser=True)
        first, second = make_model("Bracket"), make_model("Bracket")
        content_changed(db_session, "model", [first.id, second.id])
        db_session.commit()
        drain_search(db_session)
        state = db_session.get(SearchLexicalState, 1)
        assert (state.document_count, state.total_length) == (2, 6)
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 2

        update_model(first.id, ModelUpdate(name="Support"), actor, db_session)
        drain_search(db_session)
        db_session.expire_all()
        assert (state.document_count, state.total_length) == (2, 6)
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1
        assert db_session.get(SearchLexicalTerm, "support").document_frequency == 1

        soft_delete_model(db_session, first)
        drain_search(db_session)
        db_session.expire_all()
        assert (state.document_count, state.total_length) == (1, 3)
        assert db_session.get(SearchLexicalTerm, "support") is None
        assert db_session.get(SearchLexicalTerm, "bracket").document_frequency == 1

    def test_ranks_exact_title_above_body(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        body = make_model("Notes", description="bracket")
        title = make_model("Bracket")
        content_changed(db_session, "model", [body.id, title.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "fts5"
        assert [row.subject_id for row in result.items] == [title.id, body.id]

    def test_searches_current_content_after_edit(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        update_model(model.id, ModelUpdate(name="Hinge"), actor, db_session)
        drain_search(db_session)

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
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        soft_delete_model(db_session, model)
        drain_search(db_session)

        assert search(db_session, actor, "bracket").items == []

    def test_rolls_back_lexical_edits(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
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
        drain_search(db_session)

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "ranked_like"
        assert [row.subject_id for row in result.items] == [model.id]

    def test_preserves_content_when_native_update_fails(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        model = make_model("Bracket")
        content_changed(db_session, "model", [model.id])
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        db_session.execute(text("DROP TABLE search_passages_fts"))
        db_session.commit()
        drain_search(db_session)

        update_model(model.id, ModelUpdate(name="Hinge"), actor, db_session)
        drain_search(db_session)
        result = search(db_session, actor, "hinge")

        assert result.lexical_backend == "ranked_like"
        assert [row.subject_id for row in result.items] == [model.id]

    def test_repairs_native_index_in_bounded_pages(
        self, db_session, make_user, make_model
    ):
        actor = make_user(superuser=True)
        models = [make_model(f"Bracket {i}") for i in range(3)]
        content_changed(db_session, "model", [row.id for row in models])
        drain_search(db_session)
        assert lexical_index.rebuild_partition(db_session, limit=2) == 2
        db_session.commit()
        drain_search(db_session)
        assert lexical_index.capability(db_session) == "ranked_like"
        drain_search(db_session)
        assert lexical_index.rebuild_partition(db_session, limit=2) == 1
        db_session.commit()
        drain_search(db_session)

        result = search(db_session, actor, "bracket")

        assert result.lexical_backend == "fts5"
        assert {row.subject_id for row in result.items} == {row.id for row in models}

    def test_escapes_like_wildcards(self, db_session, make_user, make_model):
        actor = make_user(superuser=True)
        literal = make_model("50%_bracket")
        other = make_model("50mm bracket")
        content_changed(db_session, "model", [literal.id, other.id])
        db_session.commit()
        drain_search(db_session)

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
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
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
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
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
        drain_search(db_session)
        lexical_index.rebuild_partition(db_session)
        db_session.commit()
        drain_search(db_session)
        db_session.execute(text("DROP TABLE search_passages_fts"))
        db_session.commit()
        drain_search(db_session)
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
        drain_search(db_session)
        while lexical_index.rebuild_partition(db_session):
            pass
        db_session.commit()
        drain_search(db_session)
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
        drain_search(db_session)
        while lexical_index.rebuild_partition(db_session):
            pass
        db_session.commit()
        drain_search(db_session)
        statement = ordered_passages(
            db_session, "UniqueNeedle", visible_passage_ids(db_session, actor)
        )
        with sqlite_work(db_session) as large:
            after = db_session.exec(statement).all()
        assert [row[0] for row in before] == [row[0] for row in after]
        assert len(after) == 1
        assert large.instructions <= max(1000, small.instructions * 2)
