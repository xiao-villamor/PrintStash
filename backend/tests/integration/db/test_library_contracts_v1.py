"""Fresh and upgraded databases enforce transactional catalog/edit versions."""

import pytest
from sqlalchemy import text

from app.db.library_contracts_v1 import BROWSE_TABLES
from app.modules.library.model_views.browse import _revision


class TestInstall:
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
