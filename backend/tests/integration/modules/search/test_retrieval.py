"""A real active generation supplies bounded semantic results with fresh authorization."""

from urllib.parse import parse_qs, urlsplit

import pytest
from printstash_core.search.passages import SubjectType
from sqlmodel import select

from app.core.errors import OperationError
from app.db.models import Document, PassageVector, SearchGenerationLease
from app.modules.inference.query import close_queries
from app.modules.search import configuration, generations, semantic
from app.modules.search.retrieval import search
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal


@pytest.fixture(autouse=True)
def projection():
    from app.db.projections import bind_content_projection
    from app.modules.search.projection import LibraryProjection

    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


@pytest.fixture
def hybrid_library(
    db_session, generation_setup, healthy_embeddings, advance_generation, make_model
):
    close_queries()
    actor, endpoint = generation_setup
    model = make_model("Benchy boat")
    proposal = generations.prepare(
        db_session,
        actor,
        GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
    )
    advance_generation(proposal.id)
    document = db_session.exec(select(Document)).one()
    healthy_embeddings.requests.clear()
    yield actor, endpoint, proposal, document, model
    close_queries()


class TestSearch:
    @pytest.mark.parametrize(
        "revoke,available", [(False, True), (True, False)], ids=["withdrawn", "revoked"]
    )
    def test_reauthorizes_retries_after_vector_withdrawal(
        self, db_session, hybrid_library, monkeypatch, revoke, available
    ):
        from sqlmodel import delete

        actor, *_ = hybrid_library
        leg = semantic.registry(db_session, configuration.settings(db_session))[0]
        query = semantic.vector_store.query
        calls = []

        def withdraw(session, **kwargs):
            result = query(session, **kwargs)
            calls.append(len(result.items))
            session.exec(
                delete(PassageVector).where(
                    PassageVector.id.in_([item.unit_id for item in result.items])
                )
            )
            if revoke:
                actor.is_active = False
                session.add(actor)
            session.commit()
            return result

        monkeypatch.setattr(semantic.vector_store, "query", withdraw)
        result = semantic.retrieve(
            db_session,
            actor.id,
            actor.auth_version,
            "assembly",
            leg,
            types=tuple(SubjectType),
        )

        assert result.available is available
        assert result.passages == ()
        assert calls[0] > 0
        assert len(calls) <= 2
        assert db_session.exec(select(SearchGenerationLease)).all() == []

    def test_rejects_unavailable_source_models(self, db_session, hybrid_library):
        actor, *_ = hybrid_library

        with pytest.raises(OperationError, match="search_model_unavailable"):
            search(db_session, actor, "", source_model_id=999999)

    @pytest.mark.parametrize("limit", [0, 101], ids=["zero", "over-cap"])
    def test_bounds_direct_result_limits(self, db_session, hybrid_library, limit):
        actor, *_ = hybrid_library

        with pytest.raises(ValueError, match="search_page_limit"):
            search(db_session, actor, "part", limit=limit)

    def test_rejects_nonimage_upload_inputs(self, db_session, hybrid_library):
        from printstash_core.inference import EmbeddingInput

        actor, *_ = hybrid_library

        with pytest.raises(ValueError, match="search_image_required"):
            search(db_session, actor, "", image=EmbeddingInput("text", text="part"))

    def test_drops_semantic_output_after_consent_revocation(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, *_ = hybrid_library
        leg = semantic.registry(db_session, configuration.settings(db_session))[0]

        def revoke():
            configuration.update(db_session, SearchSettings(enabled=False))
            db_session.commit()

        healthy_embeddings.before_reply = revoke
        result = semantic.retrieve(
            db_session,
            actor.id,
            actor.auth_version,
            "instructions",
            leg,
            types=tuple(SubjectType),
        )

        assert result.available is False
        assert result.passages == ()
        assert len(healthy_embeddings.requests) == 1
        assert db_session.exec(select(SearchGenerationLease)).all() == []

    def test_reuses_text_vectors_for_model_queries(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, _, _, document, model = hybrid_library
        result = search(db_session, actor, "", source_model_id=model.id)
        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            ("document", document.id)
        ]
        assert result.semantic_ready is True
        assert "semantic_text" in result.legs
        assert healthy_embeddings.requests == []

    @pytest.mark.parametrize(
        "revocation", ["missing", "inactive", "auth_version", "consent"]
    )
    def test_denies_stale_semantic_admission(
        self, db_session, hybrid_library, healthy_embeddings, revocation
    ):
        actor, *_ = hybrid_library
        leg = semantic.registry(db_session, configuration.settings(db_session))[0]
        user_id, auth_version = actor.id, actor.auth_version
        if revocation == "missing":
            user_id += 1000
        elif revocation == "inactive":
            actor.is_active = False
            db_session.add(actor)
        elif revocation == "auth_version":
            auth_version += 1
        else:
            configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()
        result = semantic.retrieve(
            db_session, user_id, auth_version, "Benchy", leg, types=tuple(SubjectType)
        )
        assert result.available is False
        assert result.passages == ()
        assert healthy_embeddings.requests == []
        assert db_session.exec(select(SearchGenerationLease)).all() == []

    @pytest.mark.parametrize(
        "invalid, code",
        [
            ("image", "embedding_image_unavailable"),
            ("long", "embedding_input_limit_exceeded"),
        ],
    )
    def test_rejects_incompatible_semantic_inputs(
        self, db_session, hybrid_library, healthy_embeddings, invalid, code
    ):
        from printstash_core.inference import EmbeddingInput

        from app.modules.search.text_inputs import TextRecipe

        actor, *_ = hybrid_library
        leg = semantic.registry(db_session, configuration.settings(db_session))[0]
        value = (
            EmbeddingInput("image", rgb=b"\0\0\0", width=1, height=1)
            if invalid == "image"
            else "x" * (TextRecipe.for_space(leg.space).max_input_characters + 1)
        )
        result = semantic.retrieve(
            db_session,
            actor.id,
            actor.auth_version,
            value,
            leg,
            types=tuple(SubjectType),
        )
        assert result.available is False
        assert result.error_code == code
        assert healthy_embeddings.requests == []

    def test_reauthorizes_cached_query_vectors(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, *_ = hybrid_library
        assert search(db_session, actor, "assembly instructions").items
        assert len(healthy_embeddings.requests) == 1
        actor.is_superuser = False
        db_session.add(actor)
        db_session.commit()

        result = search(db_session, actor, "assembly instructions")

        assert result.items == []
        assert len(healthy_embeddings.requests) == 1

    def test_expires_cursors_after_ranking_changes(self, db_session, hybrid_library):
        actor, *_ = hybrid_library
        first = search(db_session, actor, "assembly instructions", limit=1)
        assert first.next_cursor
        configuration.update(
            db_session, SearchSettings(enabled=True, semantic_weight=2)
        )
        db_session.commit()

        with pytest.raises(OperationError, match="search_cursor_invalid"):
            search(
                db_session,
                actor,
                "assembly instructions",
                cursor=first.next_cursor,
                limit=1,
            )

    def test_retries_admission_after_concurrent_cutover(
        self, db_session, hybrid_library, advance_generation
    ):
        from sqlalchemy import event

        from app.db.session import get_session_factory

        actor, endpoint, _, _, _ = hybrid_library
        replacement = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id,
                quantization="int8",
                index_backend="numpy",
                auto_activate=False,
            ),
        )
        advance_generation(replacement.id)
        activated = []

        def cutover(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            if (
                not activated
                and "FROM index_generations JOIN embedding_spaces" in statement
                and "embedding_spaces.profile" in statement
            ):
                activated.append(True)
                with get_session_factory().scoped_session() as session:
                    generations.activate(
                        session, replacement.id, replacement.version_token
                    )

        engine = db_session.get_bind()
        event.listen(engine, "after_cursor_execute", cutover)
        try:
            result = search(db_session, actor, "assembly instructions")
        finally:
            event.remove(engine, "after_cursor_execute", cutover)

        assert activated == [True]
        assert result.generations == [replacement.id]
        assert len(result.items) == 2
        assert result.semantic_ready
        assert result.degraded == []

    def test_reports_bounded_candidate_coverage(
        self, db_session, hybrid_library, make_document, advance_indexing
    ):
        from app.db.projections import content_changed

        actor, *_ = hybrid_library
        documents = [make_document(f"Workshop note {number}") for number in range(101)]
        content_changed(db_session, "document", [document.id for document in documents])
        db_session.commit()
        advance_indexing(40)

        result = search(
            db_session,
            actor,
            "semantic-only-query",
            types=(SubjectType.DOCUMENT,),
            limit=100,
        )

        assert len(result.items) == 100
        assert result.truncated
        assert result.next_cursor is None

    def test_selects_the_declared_space_floor(self, db_session, hybrid_library):
        from app.modules.search import semantic

        actor, _, proposal, _, _ = hybrid_library
        configuration.update(
            db_session,
            SearchSettings(
                enabled=True,
                semantic_floor=-1,
                semantic_floors={proposal.config_hash: 0.9},
            ),
        )
        db_session.commit()

        assert (
            semantic.registry(db_session, configuration.settings(db_session))[0].floor
            == 0.9
        )

    def test_refuses_inference_without_visible_vectors(
        self, db_session, hybrid_library, healthy_embeddings, make_user
    ):
        reader = make_user()

        result = search(db_session, reader, "assembly instructions")

        assert result.items == []
        assert healthy_embeddings.requests == []
        assert result.outcome == "no_results"

    def test_expires_cursors_after_permission_changes(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, *_ = hybrid_library
        first = search(db_session, actor, "assembly instructions", limit=1)
        assert first.next_cursor
        actor.is_superuser = False
        db_session.add(actor)
        db_session.commit()
        healthy_embeddings.requests.clear()

        with pytest.raises(OperationError, match="search_cursor_invalid"):
            search(
                db_session,
                actor,
                "assembly instructions",
                cursor=first.next_cursor,
                limit=1,
            )

        assert healthy_embeddings.requests == []

    @pytest.mark.asyncio
    async def test_returns_lexical_results_by_query_deadline(
        self, app, db_session, hybrid_library, healthy_embeddings
    ):
        import asyncio
        from threading import Event
        from time import monotonic

        import httpx

        from app.modules.identity.auth import create_access_token

        actor, _, _, _, model = hybrid_library
        configuration.update(
            db_session, SearchSettings(enabled=True, query_timeout_seconds=0.5)
        )
        db_session.commit()
        release = Event()
        entered = Event()

        def block_endpoint():
            entered.set()
            release.wait(5)

        healthy_embeddings.before_reply = block_endpoint
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            pending = asyncio.create_task(
                client.get(
                    "/api/v1/search",
                    params={"q": "Benchy"},
                    headers={
                        "Authorization": "Bearer "
                        + create_access_token(actor.id, actor.username, scope="admin")
                    },
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                # The configured inference deadline starts at provider admission.
                # Cold SQL compilation/authentication precedes that budget; the
                # separate scale benchmark measures the entire HTTP request.
                admitted = monotonic()
                health = await asyncio.wait_for(client.get("/api/v1/health"), 0.25)
                assert health.status_code == 200, health.text
                assert not pending.done()
                response = await asyncio.wait_for(pending, 1)
                assert response.status_code == 200, response.text
                result = response.json()
                assert not release.is_set()
                assert monotonic() - admitted < 1
                assert [item["subject_id"] for item in result["items"]] == [model.id]
                assert result["legs"] == ["lexical"]
                assert result["degraded"] == ["search_semantic_unavailable"]
            finally:
                release.set()
                await pending

    def test_preserves_an_inflight_generation(
        self, db_session, hybrid_library, healthy_embeddings, advance_generation
    ):
        actor, endpoint, first, _, _ = hybrid_library
        replacement = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id,
                quantization="int8",
                index_backend="numpy",
                auto_activate=False,
            ),
        )
        advance_generation(replacement.id)
        activation_errors = []

        def activate():
            assert len(db_session.exec(select(SearchGenerationLease)).all()) == 1
            try:
                generations.activate(
                    db_session, replacement.id, replacement.version_token
                )
            except Exception as exc:
                activation_errors.append(exc)
                raise

        healthy_embeddings.before_reply = activate
        result = search(db_session, actor, "assembly instructions")

        assert activation_errors == []
        assert len(result.items) == 2
        assert result.generations == [first.id]
        assert result.semantic_ready
        assert db_session.exec(select(SearchGenerationLease)).all() == []

    def test_excludes_trashed_subjects(self, db_session, hybrid_library):
        from app.modules.library.trash import soft_delete_model

        actor, _, _, document, model = hybrid_library
        soft_delete_model(db_session, model)

        result = search(db_session, actor, "assembly instructions")

        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            (SubjectType.DOCUMENT, document.id)
        ]

    def test_retrieves_semantic_matches(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, _, proposal, document, model = hybrid_library

        result = search(db_session, actor, "instructions to assemble the part")

        assert {(item.subject_type, item.subject_id) for item in result.items} == {
            (SubjectType.DOCUMENT, document.id),
            (SubjectType.MODEL, model.id),
        }
        assert all(item.evidence[0].leg == "semantic_text" for item in result.items)
        assert result.semantic_ready
        assert result.generations == [proposal.id]
        assert result.outcome == "results"
        assert len(healthy_embeddings.requests) == 1

    def test_rejects_weak_dense_neighbors(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, *_ = hybrid_library
        healthy_embeddings.vector = [-1, 0, 0, 0]

        result = search(db_session, actor, "unrelated astronomy")

        assert result.items == []
        assert result.outcome == "no_strong_matches"
        assert result.semantic_ready

    @pytest.mark.parametrize(
        "options",
        [{"mode": "lexical"}, {"instant": True}, {"legs": ("lexical",)}],
        ids=["mode", "instant", "leg-selection"],
    )
    def test_disables_query_inference_for_lexical_mode(
        self, db_session, hybrid_library, healthy_embeddings, options
    ):
        actor, _, _, _, model = hybrid_library

        result = search(db_session, actor, "Benchy", **options)

        assert [item.subject_id for item in result.items] == [model.id]
        assert result.legs == ["lexical"]
        assert not result.semantic_ready
        assert healthy_embeddings.requests == []

    def test_filters_subject_types_before_ranking(self, db_session, hybrid_library):
        actor, _, _, document, _ = hybrid_library

        result = search(
            db_session, actor, "assembly instructions", types=(SubjectType.DOCUMENT,)
        )

        assert [(item.subject_type, item.subject_id) for item in result.items] == [
            (SubjectType.DOCUMENT, document.id)
        ]

    def test_rechecks_permissions_after_inference(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, *_ = hybrid_library

        def revoke():
            actor.is_superuser = False
            db_session.add(actor)
            db_session.commit()

        healthy_embeddings.before_reply = revoke
        result = search(db_session, actor, "assembly instructions")

        assert result.items == []
        assert db_session.exec(select(SearchGenerationLease)).all() == []

    def test_keeps_disabled_ai_lexical(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        actor, _, _, _, model = hybrid_library
        configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()

        result = search(db_session, actor, "Benchy")

        assert [item.subject_id for item in result.items] == [model.id]
        assert result.legs == ["lexical"]
        assert healthy_embeddings.requests == []

    def test_expires_cursors_after_generation_switch(
        self, db_session, hybrid_library, advance_generation
    ):
        actor, endpoint, _, _, _ = hybrid_library
        first = search(db_session, actor, "assembly instructions", limit=1)
        assert first.next_cursor
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, quantization="int8", index_backend="numpy"
            ),
        )
        advance_generation(proposal.id)

        with pytest.raises(OperationError, match="search_cursor_expired"):
            search(
                db_session,
                actor,
                "assembly instructions",
                limit=1,
                cursor=first.next_cursor,
            )

    def test_excludes_stale_vectors(self, db_session, hybrid_library):
        actor, *_ = hybrid_library
        for row in db_session.exec(select(PassageVector)).all():
            row.input_hash = "0" * 64
            db_session.add(row)
        db_session.commit()

        assert search(db_session, actor, "assembly instructions").items == []

    def test_degrades_after_active_vector_corruption(self, db_session, hybrid_library):
        actor, _, _, _, model = hybrid_library
        for row in db_session.exec(select(PassageVector)).all():
            row.vector_blob = b"bad"
            db_session.add(row)
        db_session.commit()

        result = search(db_session, actor, "Benchy")

        assert [item.subject_id for item in result.items] == [model.id]
        assert result.degraded == ["search_semantic_unavailable"]

    def test_encodes_collection_links(self, db_session, make_collection, make_user):
        from app.db.projections import content_changed

        actor = make_user(superuser=True)
        collection = make_collection("Bracket & assembly")
        content_changed(db_session, "collection", [collection.id])
        db_session.commit()

        result = search(db_session, actor, "Bracket")

        assert parse_qs(urlsplit(result.items[0].href).query)["c"] == [collection.path]


class TestStructuredRetrieval:
    def test_filters_semantic_candidates_before_scoring(
        self, db_session, hybrid_library, healthy_embeddings
    ):
        from app.schemas.models import ModelFilters

        actor, _, _, _, model = hybrid_library
        result = search(
            db_session,
            actor,
            "assembly instructions",
            filters=ModelFilters(printed=True),
        )
        assert result.items == []
        assert healthy_embeddings.requests == []
        result = search(
            db_session, actor, "unrelated words", filters=ModelFilters(printed=False)
        )
        assert [item.subject_id for item in result.items] == [model.id]
        assert len(healthy_embeddings.requests) == 1
        assert result.items[0].evidence[0].leg == "semantic_text"

    def test_serves_a_filter_only_query_in_requested_order(
        self, db_session, hybrid_library, make_model
    ):
        from app.schemas.models import ModelFilters, ModelSort

        actor, *_ = hybrid_library
        first = make_model("Alpha")
        last = make_model("Zulu")
        result = search(
            db_session,
            actor,
            "",
            filters=ModelFilters(printed=False),
            sort=ModelSort.NAME_ASC,
            limit=1,
        )
        assert result.items[0].subject_id == first.id
        assert result.next_cursor
        following = search(
            db_session,
            actor,
            "",
            filters=ModelFilters(printed=False),
            sort=ModelSort.NAME_ASC,
            cursor=result.next_cursor,
        )
        assert following.items[-1].subject_id == last.id
        assert all(item.subject_type == "model" for item in following.items)
        assert result.items[0].evidence[0].field == "filters"

    @pytest.mark.parametrize("change", ["filters", "sort"])
    def test_expires_a_cursor_when_structured_context_changes(
        self, db_session, hybrid_library, make_model, change
    ):
        from app.schemas.models import ModelFilters, ModelSort

        actor, *_ = hybrid_library
        make_model("Additional model")
        result = search(
            db_session, actor, "", filters=ModelFilters(printed=False), limit=1
        )
        assert result.next_cursor
        with pytest.raises(OperationError, match="search_cursor_invalid"):
            search(
                db_session,
                actor,
                "",
                filters=ModelFilters()
                if change == "filters"
                else ModelFilters(printed=False),
                sort=ModelSort.NAME_ASC if change == "sort" else ModelSort.RELEVANCE,
                cursor=result.next_cursor,
            )
