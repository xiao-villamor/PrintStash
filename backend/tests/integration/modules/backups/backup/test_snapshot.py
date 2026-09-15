"""PostgreSQL uses the existing portable archive and restore marker contract."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlmodel import Session, SQLModel

from alembic import command
from app.core.config import _overlay
from app.db.migrate import _alembic_config
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.modules.backups.backup import snapshot
from tests.containers import postgres_url
from tests.factories import (
    build_embedding_space,
    build_file,
    build_index_generation,
    build_model,
    build_passage_vector,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_backup(backup_env, monkeypatch):
    root = make_url(normalize_database_url(postgres_url()))
    base = create_engine(root)
    schema = "backup_" + uuid4().hex
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = root.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    config = _alembic_config(url.render_as_string(hide_password=False))
    command.stamp(config, "head")
    previous = get_session_factory()
    monkeypatch.setitem(_overlay, "db_url", url.render_as_string(hide_password=False))
    override_session_factory(SQLiteSessionFactory(engine))
    try:
        yield engine
    finally:
        override_session_factory(previous)
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestPostgresSnapshot:
    def test_restores_native_floats_from_portable_backup(self, postgres_backup):
        engine = postgres_backup
        with Session(engine) as session:
            model = build_model(session, "Preserved")
            file = build_file(session, model)
            model.thumbnail_file_id = file.id
            session.add(model)
            session.commit()
            space = build_embedding_space(session)
            generation = build_index_generation(session, space)
            vector = build_passage_vector(session, generation, file)
            identity, blob = vector.id, vector.vector_blob
        with snapshot._sqlite_snapshot_file() as path:
            # Snapshot is portable SQLite, not a pgvector-dependent SQL dump.
            portable = create_engine(f"sqlite:///{path}")
            try:
                with portable.connect() as connection:
                    assert (
                        connection.execute(
                            text("SELECT vector_blob FROM passage_vectors")
                        ).scalar_one()
                        == blob
                    )
            finally:
                portable.dispose()
            with engine.begin() as connection:
                connection.execute(text("UPDATE models SET name='Changed'"))
                connection.execute(text("DELETE FROM passage_vectors"))
            snapshot._restore_database_from_path(path)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT name FROM models")).scalar_one()
                == "Preserved"
            )
            assert connection.execute(
                text("SELECT id,vector_blob FROM passage_vectors")
            ).one() == (identity, blob)
        assert snapshot.database_backup_capability().restore_supported
        assert snapshot._database_snapshot_size() > 0
