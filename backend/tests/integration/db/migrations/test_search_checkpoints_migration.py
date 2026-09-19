"""Dependency inventories and repair cursors upgrade around existing library data."""

from alembic.config import Config
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

from alembic import command
from app.db.models import Model, SearchPassage, SearchReconciliationState
from app.modules.search.reconciliation import reconcile_partition
from tests.factories import build_model, build_search_passage
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_INI


class TestSearchCheckpointsMigration:
    def test_upgrades_projection_checkpoints_with_content(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'checkpoints.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "bf435683b126")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(connection, "models", name="Existing bracket", slug="existing", hash="a" * 64)
            command.upgrade(config, "head")
            with Session(engine) as session:
                reconcile_partition(session, SubjectType.MODEL)
                session.commit()
            with Session(engine) as session:
                assert session.exec(select(Model.name)).all() == ["Existing bracket"]
                assert (
                    session.exec(select(SearchPassage.text)).one()
                    == "Title: Existing bracket"
                )
                assert (
                    session.exec(select(SearchReconciliationState.subject_type)).one()
                    == "model"
                )
        finally:
            engine.dispose()

    def test_downgrades_projection_checkpoints_with_content(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'checkpoints.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with Session(engine) as session:
                model = build_model(session, "Existing bracket")
                build_search_passage(
                    session, SearchSubject(SubjectType.MODEL, model.id)
                )
            command.downgrade(config, "bf435683b126")
            with Session(engine) as session:
                assert session.exec(select(Model.name)).all() == ["Existing bracket"]
                assert (
                    session.exec(select(SearchPassage.text)).one()
                    == "Title: Stored passage"
                )
            assert "search_dependencies" not in inspect(engine).get_table_names()
            assert (
                "search_reconciliation_states" not in inspect(engine).get_table_names()
            )
        finally:
            engine.dispose()
