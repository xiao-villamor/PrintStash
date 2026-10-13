"""Fresh and upgraded databases enforce transactional catalog/edit versions."""

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import and_, select, text, update
from sqlmodel import Session, SQLModel

from app.core.errors import OperationError
from app.db.library_contracts_v1 import BROWSE_TABLES
from app.db.models import User
from app.modules.library import commands, multipart_models, provenance
from app.modules.library.model_views.browse import _revision, page_items
from app.schemas.library_browse import BrowseQuery
from app.schemas.multipart_models import MultipartChoiceWrite, MultipartPartWrite
from tests.factories import (
    identity,
    library,
    ops,
    printers,
    search,
    similarity,
    vault_migrations,
)
from tests.factories import provenance as provenance_fixtures


@pytest.fixture
def catalog_writer(db_session: Session, table: str) -> User:
    """Persist one valid target of a catalog writer through existing builders."""
    user = identity.build_user(db_session, superuser=True)
    folder = library.build_collection(db_session)
    first = library.build_model(db_session, "Anchor")
    second = library.build_model(db_session, "Tail")
    group = library.build_multipart_model(db_session)
    artifact = library.build_file(db_session, first)
    tag = library.build_tag(db_session)
    printer = printers.build_printer(db_session)
    source = provenance_fixtures.build_provenance_source(db_session, first)
    passage = search.build_search_passage(
        db_session, SearchSubject(SubjectType.MODEL, first.id)
    )

    def parts():
        return multipart_models.replace_parts(
            db_session,
            user,
            group,
            [
                MultipartPartWrite(
                    name="Body", choices=[MultipartChoiceWrite(model_id=first.id)]
                )
            ],
        )

    seeders = {
        "models": lambda: first,
        "multipart_models": lambda: group,
        "multipart_parts": parts,
        "multipart_model_choices": parts,
        "collections": lambda: folder,
        "tags": lambda: tag,
        "model_tags": lambda: library.tag_model(db_session, first, tag),
        "collection_tags": lambda: library.tag_collection(db_session, folder, tag),
        "file_tags": lambda: library.tag_file(db_session, artifact, tag),
        "multipart_model_tags": lambda: library.tag_multipart_model(
            db_session, group, tag
        ),
        "model_stars": lambda: commands.star_model(first.id, user, db_session),
        "multipart_model_stars": lambda: library.build_multipart_model_star(
            db_session, user, group
        ),
        "files": lambda: artifact,
        "metadata": lambda: library.build_metadata(db_session, artifact),
        "print_jobs": lambda: printers.build_print_job(db_session, artifact),
        "printer_files": lambda: printers.build_printer_file(
            db_session, printer, file=artifact
        ),
        "printers": lambda: printer,
        "documents": lambda: ops.build_document(db_session),
        "collection_permissions": lambda: identity.grant_collection_role(
            db_session, user, folder
        ),
        "users": lambda: user,
        "model_provenance_sources": lambda: source,
        "model_provenance_fields": lambda: provenance.set_user_override(
            db_session,
            provenance_source_id=source.id,
            field_name="title",
            value="Override",
        ),
        "model_source_covers": lambda: provenance_fixtures.build_cover(
            db_session, source
        ),
        "similarity_candidates": lambda: similarity.build_similarity_candidate(
            db_session, first, second
        ),
        "similarity_review_decisions": lambda: similarity.build_similarity_decision(
            db_session,
            similarity.build_similarity_candidate(db_session, first, second),
            user,
        ),
        "search_passages": lambda: passage,
        "search_lexical_postings": lambda: search.build_search_lexical_posting(
            db_session, passage
        ),
        "search_lexical_terms": lambda: search.build_search_lexical_term(db_session),
        "search_lexical_state": lambda: search.build_search_lexical_state(db_session),
        "index_generations": lambda: similarity.build_index_generation(
            db_session, similarity.build_embedding_space(db_session)
        ),
        "user_search_preferences": lambda: search.build_user_search_preferences(
            db_session, user
        ),
        "vault_generations": lambda: vault_migrations.build_vault_generation(
            db_session, vault_migrations.build_vault_migration(db_session)
        ),
    }
    assert set(seeders) == set(BROWSE_TABLES)
    seeders[table]()
    db_session.commit()
    return user


class TestInstall:
    @pytest.mark.parametrize("table", BROWSE_TABLES, ids=BROWSE_TABLES)
    def test_rejects_continuation_after_each_catalog_dependency_writer(
        self, db_session: Session, table: str, catalog_writer: User
    ):
        page = page_items(db_session, catalog_writer, BrowseQuery(limit=1))
        assert page.next_cursor is not None
        target = SQLModel.metadata.tables[table]
        keys = list(target.primary_key.columns)
        row = db_session.execute(select(*keys).limit(1)).one()
        identity = and_(*(key == value for key, value in zip(keys, row, strict=True)))
        # The contract conservatively observes every committed writer, including
        # no-op updates. All PKs and real FK/check constraints remain in force.
        result = db_session.execute(
            update(target).where(identity).values({keys[0].name: keys[0]})
        )
        assert result.rowcount == 1
        db_session.commit()

        with pytest.raises(OperationError, match="browse_refresh_required"):
            page_items(
                db_session,
                catalog_writer,
                BrowseQuery(limit=1, cursor=page.next_cursor),
            )

    @pytest.mark.parametrize("table", BROWSE_TABLES, ids=BROWSE_TABLES)
    def test_covers_every_dependency_writer(self, db_session, table):
        rows = db_session.execute(
            text(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:table AND name LIKE 'ps_browse_%'"
            ),
            {"table": table},
        ).all()

        assert len(rows) == 3
        assert {name for name, _ddl in rows} == {
            f"ps_browse_{table}_{action}_v1"
            for action in ("insert", "update", "delete")
        }
        assert all(
            "INSERT INTO library_revision" in ddl and "ON CONFLICT" in ddl
            for _name, ddl in rows
        )

    @pytest.mark.parametrize(
        "operation", ["insert", "update", "delete"], ids=["insert", "update", "delete"]
    )
    def test_advances_revision_with_raw_catalog_sql(
        self, db_session, make_model, operation
    ):
        model = make_model()
        before = _revision(db_session)
        statement = {
            "insert": "INSERT INTO models (name,slug,hash,next_file_version,created_at,updated_at) VALUES ('Raw','raw','raw-hash',1,'2026-01-01','2026-01-01')",
            "update": "UPDATE models SET name='Changed' WHERE id=:id",
            "delete": "DELETE FROM models WHERE id=:id",
        }[operation]

        db_session.execute(text(statement), {"id": model.id})
        db_session.commit()

        assert _revision(db_session) != before

    def test_keeps_revision_in_writer_transaction(self, db_session, make_model):
        model = make_model()
        before = _revision(db_session)
        db_session.execute(
            text("UPDATE models SET name='Rolled back' WHERE id=:id"), {"id": model.id}
        )
        db_session.rollback()

        after = _revision(db_session)

        assert after == before


class TestUpgrade:
    def test_upgrades_existing_rows(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlmodel import Session

        from alembic import command
        from app.db.migrate import _alembic_config
        from app.db.models import Document, Model, MultipartModel
        from tests.factories.migration_rows import seed_schema_row

        url = f"sqlite:///{tmp_path / 'upgrade.sqlite'}"
        engine = create_engine(url)
        config = _alembic_config(url)
        command.upgrade(config, "8298455ff341")
        ids = (1, 1, 1)
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Preserved Model",
                slug="preserved-model",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "multipart_models",
                id=1,
                name="Preserved Set",
                slug="preserved-set",
            )
            seed_schema_row(
                connection,
                "documents",
                id=1,
                name="Preserved Guide",
                kind="MARKDOWN",
                body="Existing guide",
            )

        command.upgrade(config, "head")

        with Session(engine) as session:
            model = session.get(Model, ids[0])
            group = session.get(MultipartModel, ids[1])
            document = session.get(Document, ids[2])
            assert (model.name, group.name, document.name) == (
                "Preserved Model",
                "Preserved Set",
                "Preserved Guide",
            )
            assert (model.edit_version, group.edit_version, document.edit_version) == (
                1,
                1,
                1,
            )
            before = _revision(session)
            session.execute(
                text("UPDATE documents SET body='After upgrade' WHERE id=:id"),
                {"id": ids[2]},
            )
            session.commit()
            assert _revision(session) != before
            session.refresh(document)
            assert document.edit_version > 1
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
        with Session(engine) as session:
            assert session.get(Document, ids[2]).body == "After upgrade"
        engine.dispose()
