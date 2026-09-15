"""Supported PostgreSQL preserves the same durable passage invariants as SQLite."""

from uuid import uuid4

import pytest
from alembic.config import Config
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from alembic import command
from app.db.models.search import SearchPassage
from app.db.projections import (
    batch_content_changes,
    bind_content_projection,
    content_changed,
)
from app.db.url import normalize_database_url
from app.modules.search.passages import sync_subject
from app.modules.search.projection import LibraryProjection
from app.modules.search.reconciliation import reconcile_partition
from tests.containers import postgres_url
from tests.factories import (
    build_collection,
    build_document,
    build_model,
    build_search_passage,
    build_tag,
    tag_collection,
)
from tests.paths import ALEMBIC_INI


@pytest.fixture
def passage_engine():
    schema = "search_passages_" + uuid4().hex
    base_url = make_url(normalize_database_url(postgres_url()))
    base = create_engine(base_url)
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    try:
        SQLModel.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "0118bda3e719")
        yield engine, config
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestSearchPassages:
    def test_batch_rollback_preserves_the_previous_publication(self, passage_engine):
        engine, config = passage_engine
        command.upgrade(config, "head")
        previous = bind_content_projection(LibraryProjection())
        try:
            with Session(engine) as session:
                model = build_model(session, "Original")
                with batch_content_changes(session):
                    content_changed(session, "model", [model.id])
                    model.name = "Committed"
                    session.add(model)
                    content_changed(session, "model", [model.id])
                session.commit()
                with batch_content_changes(session):
                    model.name = "Rolled back"
                    session.add(model)
                    content_changed(session, "model", [model.id])
                session.rollback()
            with Session(engine) as session:
                assert (
                    session.exec(select(SearchPassage.text)).one() == "Title: Committed"
                )
        finally:
            bind_content_projection(previous)

    def test_projects_existing_models_after_upgrade(self, passage_engine):
        engine, config = passage_engine
        with Session(engine) as session:
            model = build_model(session, "Dragón", description="Print without supports")
            subject = SearchSubject(SubjectType.MODEL, model.id)

        command.upgrade(config, "head")
        with Session(engine) as session:
            sync_subject(session, subject)
            session.commit()

        with Session(engine) as session:
            assert (
                session.exec(select(SearchPassage.text)).one()
                == "Title: Dragón\nDescription: Print without supports"
            )

    def test_rejects_duplicate_passage_identity(self, passage_engine):
        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            model = build_model(session)
            subject = SearchSubject(SubjectType.MODEL, model.id)
            build_search_passage(session, subject)

            with pytest.raises(IntegrityError, match="uq_search_passage_identity"):
                build_search_passage(session, subject)
            session.rollback()

            assert len(session.exec(select(SearchPassage)).all()) == 1

    def test_repairs_a_body_change_without_a_timestamp(self, passage_engine):
        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            doc = build_document(session, "Guide", body="Before")
            reconcile_partition(session, SubjectType.DOCUMENT)
            session.commit()
            doc.body = "After"
            session.add(doc)
            session.commit()
            reconcile_partition(session, SubjectType.DOCUMENT)
            session.commit()
        with Session(engine) as session:
            assert (
                session.exec(select(SearchPassage.text)).one()
                == "Title: Guide\nBody: After"
            )

    def test_projects_inherited_tag_deletion(self, passage_engine):
        from sqlalchemy import delete

        from app.db.models import CollectionTagLink

        engine, config = passage_engine
        command.upgrade(config, "head")
        previous = bind_content_projection(LibraryProjection())
        try:
            with Session(engine) as session:
                collection = build_collection(session, "Parts")
                model = build_model(session, collection=collection)
                tag = build_tag(session, "flexible")
                tag_collection(session, collection, tag)
                content_changed(session, "model", [model.id])
                session.commit()
                assert (
                    "Tags: flexible" in session.exec(select(SearchPassage.text)).one()
                )
                session.exec(
                    delete(CollectionTagLink).where(CollectionTagLink.tag_id == tag.id)
                )
                content_changed(session, "tag", [tag.id])
                session.commit()
            with Session(engine) as session:
                assert "flexible" not in "\n".join(
                    session.exec(select(SearchPassage.text)).all()
                )
        finally:
            bind_content_projection(previous)

    def test_ranks_postgres_with_real_bm25(self, passage_engine):
        from app.modules.search.lexical_index import rebuild_partition
        from app.modules.search.lexical_query import ordered_passages

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            body = build_model(session, "Notes", description="bracket")
            title = build_model(session, "Bracket")
            for model in (body, title):
                sync_subject(session, SearchSubject(SubjectType.MODEL, model.id))
            rebuild_partition(session)
            session.commit()
            rows = session.exec(
                ordered_passages(session, "bracket", select(SearchPassage.id))
            ).all()
            subjects = [
                session.get(SearchPassage, id).subject_id for id, _score in rows
            ]
            assert subjects == [title.id, body.id]
            assert rows[0][1] > rows[1][1] > 0

    def test_maintains_postgres_statistics_after_delete(self, passage_engine):
        from app.core.time import utcnow
        from app.db.models import SearchLexicalState, SearchLexicalTerm
        from app.modules.search.lexical_index import rebuild_partition

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            model = build_model(session, "Bracket")
            subject = SearchSubject(SubjectType.MODEL, model.id)
            sync_subject(session, subject)
            rebuild_partition(session)
            session.commit()
            assert session.get(SearchLexicalState, 1).document_count == 1
            assert session.get(SearchLexicalTerm, "bracket").document_frequency == 1
            model.name = "Support"
            session.add(model)
            session.flush()
            sync_subject(session, subject)
            session.commit()
            assert session.get(SearchLexicalState, 1).total_length == 3
            assert session.get(SearchLexicalTerm, "bracket") is None
            assert session.get(SearchLexicalTerm, "support").document_frequency == 1
            model.deleted_at = utcnow()
            session.add(model)
            session.flush()
            sync_subject(session, subject)
            session.commit()
        with Session(engine) as session:
            state = session.get(SearchLexicalState, 1)
            assert (state.document_count, state.total_length) == (0, 0)
            assert session.get(SearchLexicalTerm, "bracket") is None
            assert session.get(SearchLexicalTerm, "support") is None

    def test_hides_private_member_context_on_postgres(self, passage_engine):
        from app.db.models import CollectionRole
        from app.modules.library.multipart_models import save
        from app.modules.search.lexical_index import rebuild_partition
        from app.modules.search.retrieval import search
        from app.schemas.multipart_models import (
            MultipartChoiceWrite,
            MultipartPartWrite,
        )
        from tests.factories import (
            build_multipart_model,
            build_user,
            grant_collection_role,
        )

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            admin = build_user(session, superuser=True)
            viewer = build_user(session)
            public = build_collection(session, "Shared")
            private = build_collection(session, "Private")
            member = build_model(session, "Secretprototype", collection=private)
            aggregate = build_multipart_model(session, "Assembly", collection=public)
            grant_collection_role(session, viewer, public, CollectionRole.VIEW)
            save(
                session,
                admin,
                aggregate,
                [
                    MultipartPartWrite(
                        name="Leg", choices=[MultipartChoiceWrite(model_id=member.id)]
                    )
                ],
            )
            sync_subject(session, SearchSubject(SubjectType.MODEL, member.id))
            sync_subject(
                session, SearchSubject(SubjectType.MULTIPART_MODEL, aggregate.id)
            )
            rebuild_partition(session)
            session.commit()

            assert search(session, viewer, "Secretprototype").items == []
            assert [
                row.subject_id for row in search(session, viewer, "Assembly").items
            ] == [aggregate.id]

    def test_indexes_one_caption_recipe_with_bm25(self, passage_engine):
        from app.db.models import SearchLexicalState
        from app.modules.search.captions import patch
        from app.modules.search.lexical_index import rebuild_partition
        from app.modules.search.lexical_query import ordered_passages
        from app.schemas.captions import CaptionPatch
        from tests.factories import build_subject_caption, build_user

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            actor = build_user(session, superuser=True)
            model = build_model(session, "Human title")
            subject = SearchSubject(SubjectType.MODEL, model.id)
            build_subject_caption(
                session, subject, state="edited", text="Zygomatic mount"
            )
            sync_subject(session, subject)
            rebuild_partition(session)
            session.commit()
            allowed = select(SearchPassage.id)
            assert (
                len(session.exec(ordered_passages(session, "zygomatic", allowed)).all())
                == 1
            )
            assert (
                len(session.exec(ordered_passages(session, "human", allowed)).all())
                == 1
            )
            assert session.get(SearchLexicalState, 1).document_count == 1
            patch(session, actor, subject, CaptionPatch(action="dismiss"))
            assert (
                session.exec(ordered_passages(session, "zygomatic", allowed)).all()
                == []
            )
            assert session.get(SearchLexicalState, 1).document_count == 1


class TestStructuredHistory:
    def test_applies_joint_print_predicates_on_postgres(self, passage_engine):
        from datetime import datetime, timezone

        from app.db.models import Model, PrintJobState
        from app.modules.library.model_views.facets import facets
        from app.modules.library.model_views.filters import filtered_with_rank
        from app.schemas.models import ModelFilters
        from tests.factories import build_file, build_print_job, build_user

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            actor = build_user(session, superuser=True)
            same = build_model(session, "same job")
            split = build_model(session, "split jobs")
            for model, jobs in (
                (same, [("2026-08-12", 100, PrintJobState.COMPLETED)]),
                (
                    split,
                    [
                        ("2026-08-12", 100, PrintJobState.FAILED),
                        ("2026-08-13", 10800, PrintJobState.COMPLETED),
                        ("2026-07-12", 100, PrintJobState.COMPLETED),
                    ],
                ),
            ):
                file = build_file(session, model)
                for date, duration, state in jobs:
                    build_print_job(
                        session,
                        file,
                        state=state,
                        finished_at=datetime.fromisoformat(date).replace(
                            tzinfo=timezone.utc
                        ),
                        actual_duration_s=duration,
                    )
            filters = ModelFilters(
                printed_after="2026-08-01T00:00:00Z",
                printed_before="2026-09-01T00:00:00Z",
                print_duration_max_s=10800,
                print_outcome=["completed"],
            )
            statement, _ = filtered_with_rank(session, actor, filters)
            assert session.exec(statement.with_only_columns(Model.id)).all() == [
                same.id
            ]
            result = facets(session, actor, filters)
            assert [(item.value, item.count) for item in result.print_outcome] == [
                ("completed", 1)
            ]

    def test_ranks_separate_sparse_postings_on_postgres(self, passage_engine):
        from app.modules.search import configuration
        from app.modules.search.lexical_query import ordered_passages
        from app.schemas.inference import SearchSettings
        from tests.factories import (
            build_search_expansion,
            build_search_expansion_term,
            build_system_config,
        )

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            model = build_model(session, "bicycle")
            sync_subject(session, SearchSubject(SubjectType.MODEL, model.id))
            passage = session.exec(
                select(SearchPassage).where(SearchPassage.subject_type == "model")
            ).one()
            recipe = "4" * 64
            build_system_config(
                session,
                ai_search_settings_json=SearchSettings(
                    enabled=True,
                    local_models_enabled=True,
                    sparse_expansion_enabled=True,
                    sparse_model_id=recipe,
                ).model_dump_json(),
            )
            row = build_search_expansion(session, passage, recipe=recipe)
            build_search_expansion_term(session, row, term="bike", weight=1.6)
            session.commit()
            ranks = session.exec(
                ordered_passages(session, "bike", select(SearchPassage.id))
            ).all()
            assert ranks[0][0] == passage.id
            assert 0 < ranks[0][1] <= 0.25 / 61
            configuration.update(session, SearchSettings())
            session.commit()
            assert (
                session.exec(
                    ordered_passages(session, "bike", select(SearchPassage.id))
                ).all()
                == []
            )
            assert (
                session.exec(
                    ordered_passages(session, "bicycle", select(SearchPassage.id))
                ).all()[0][0]
                == passage.id
            )


class TestCandidateVisibility:
    def test_rechecks_contributor_visibility_on_postgres(self, passage_engine):
        import json

        from printstash_core.inference import EmbeddingSpace

        from app.core.time import utcnow
        from app.modules.search.query_context import SemanticLeg, allowed_vectors
        from app.modules.search.text_inputs import TextRecipe
        from tests.factories import (
            build_embedding_space,
            build_index_generation,
            build_passage_vector,
            build_user,
            grant_collection_role,
        )

        engine, config = passage_engine
        command.upgrade(config, "head")
        with Session(engine) as session:
            actor = build_user(session)
            public = build_collection(session, "Public")
            private = build_collection(session, "Private")
            grant_collection_role(session, actor, public)
            target = build_model(session, "Target", collection=public)
            contributor = build_model(session, "Contributor", collection=private)
            passage = build_search_passage(
                session,
                SearchSubject(SubjectType.MODEL, target.id),
                access_dependencies_json=json.dumps([["model", contributor.id]]),
            )
            stored = build_embedding_space(
                session,
                modality="text",
                profile="text",
                recipe_json=TextRecipe().encode(),
            )
            generation = build_index_generation(session, stored)
            vector = build_passage_vector(session, generation, passage=passage)
            leg = SemanticLeg(
                "semantic_text",
                generation.id,
                EmbeddingSpace(**json.loads(stored.config_json)),
                0.1,
                1,
                1,
            )
            session.commit()
            assert (
                session.exec(
                    allowed_vectors(session, actor, leg, (SubjectType.MODEL,))
                ).all()
                == []
            )
            grant_collection_role(session, actor, private)
            session.commit()
            assert session.exec(
                allowed_vectors(session, actor, leg, (SubjectType.MODEL,))
            ).all() == [vector.id]
            contributor.deleted_at = utcnow()
            session.add(contributor)
            session.commit()
            assert (
                session.exec(
                    allowed_vectors(session, actor, leg, (SubjectType.MODEL,))
                ).all()
                == []
            )
