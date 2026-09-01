"""The entity-tag schema is reversible and leaves existing Model tags intact."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlmodel import Session, select

from alembic import command
from app.db.models import Model, ModelTagLink, Tag
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "3e7ab53ac43d"
PARENT = "f3a4f173d948"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _assert_upgrade_preserves_existing_model_tags(tmp_path: Path) -> None:
    database = tmp_path / "entity-tags.sqlite"
    config = _config(database)
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as session:
        model = Model(name="Existing", slug="existing", hash="a" * 64)
        tag = Tag(name="Legacy", slug="legacy")
        session.add(model)
        session.add(tag)
        session.commit()
        session.refresh(model)
        session.refresh(tag)
        session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        session.commit()

    command.upgrade(config, REVISION)

    with Session(engine) as session:
        assert len(session.exec(select(ModelTagLink)).all()) == 1
    inspector = inspect(engine)
    assert {"collection_tags", "file_tags"} <= set(inspector.get_table_names())
    assert "ix_collection_tags_tag_id" in {
        row["name"] for row in inspector.get_indexes("collection_tags")
    }
    assert "ix_file_tags_tag_id" in {
        row["name"] for row in inspector.get_indexes("file_tags")
    }

    command.downgrade(config, PARENT)

    assert not (
        {"collection_tags", "file_tags"} & set(inspect(engine).get_table_names())
    )
    with Session(engine) as session:
        assert len(session.exec(select(ModelTagLink)).all()) == 1
    engine.dispose()


class TestEntityTagsMigration:
    def test_upgrade_preserves_existing_model_tags(self, tmp_path: Path) -> None:
        _assert_upgrade_preserves_existing_model_tags(tmp_path)
