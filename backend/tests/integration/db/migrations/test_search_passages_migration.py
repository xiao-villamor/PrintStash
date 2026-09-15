"""The additive passage migration preserves an existing SQLite library."""

from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

from alembic import command
from app.db.models import Model
from tests.factories import build_model
from tests.paths import ALEMBIC_INI

BEFORE_PASSAGES = "0118bda3e719"
PASSAGES_REVISION = "bf435683b126"


class TestSearchPassagesMigration:
    def test_upgrades_without_losing_library_content(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'passages.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, BEFORE_PASSAGES)
        engine = create_engine(url)
        with Session(engine) as session:
            build_model(session, "Existing dragon")

        command.upgrade(config, PASSAGES_REVISION)

        with Session(engine) as session:
            assert session.exec(select(Model.name)).all() == ["Existing dragon"]
        assert "search_passages" in inspect(engine).get_table_names()
        engine.dispose()

    def test_downgrades_without_losing_library_content(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'passages.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, PASSAGES_REVISION)
        engine = create_engine(url)
        with Session(engine) as session:
            build_model(session, "Existing dragon")

        command.downgrade(config, BEFORE_PASSAGES)

        with Session(engine) as session:
            assert session.exec(select(Model.name)).all() == ["Existing dragon"]
        assert "search_passages" not in inspect(engine).get_table_names()
        engine.dispose()

    @pytest.mark.parametrize(
        "url",
        ["sqlite://", "postgresql://test:test@localhost/test"],
        ids=["sqlite", "postgresql"],
    )
    def test_emits_portable_offline_schema(self, url):
        output = StringIO()
        config = Config(str(ALEMBIC_INI), output_buffer=output)
        config.set_main_option("sqlalchemy.url", url)

        command.upgrade(config, f"{BEFORE_PASSAGES}:{PASSAGES_REVISION}", sql=True)

        assert "CREATE TABLE search_passages" in output.getvalue()
        assert "uq_search_passage_identity" in output.getvalue()
        assert "ck_search_passages_text_length" in output.getvalue()
