"""A PostgreSQL installation can back up and restore through the public API."""

import asyncio
from uuid import uuid4

import pytest
from printstash_core.search.passages import SubjectType
from sqlalchemy import create_engine, make_url, text
from sqlmodel import Session, SQLModel

from alembic import command
from app.core.config import _overlay
from app.db.migrate import _alembic_config
from app.db.projections import bind_content_projection
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.modules.search.projection import LibraryProjection
from app.runtime.search import process_one
from tests.containers import postgres_url
from tests.e2e._backup_helpers import setup_and_login


@pytest.fixture
def postgres_e2e_db(e2e_db, monkeypatch):
    root = make_url(normalize_database_url(postgres_url()))
    base = create_engine(root)
    schema = "e2e_backup_" + uuid4().hex
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = root.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    command.stamp(_alembic_config(url.render_as_string(hide_password=False)), "head")
    previous = get_session_factory()
    override_session_factory(SQLiteSessionFactory(engine))
    previous_projection = bind_content_projection(LibraryProjection())
    monkeypatch.setitem(_overlay, "db_url", url.render_as_string(hide_password=False))
    try:
        with Session(engine) as session:
            yield session
    finally:
        bind_content_projection(previous_projection)
        override_session_factory(previous)
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestPostgresBackup:
    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_restores_searchable_documents_through_the_api(
        self, api, tmp_path, postgres_e2e_db
    ):
        headers = await setup_and_login(api, tmp_path)
        created = await api.post(
            "/api/v1/documents",
            headers=headers,
            json={
                "name": "Bracket instructions",
                "body": "Mount the shelf",
            },
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        backup = await api.post("/api/v1/backups", headers=headers)
        assert backup.status_code == 202, backup.text
        backup_id = backup.json()["backup_id"]
        postgres_e2e_db.execute(text("UPDATE documents SET name='Lost',body='Missing'"))
        postgres_e2e_db.commit()
        restored = await api.post(
            f"/api/v1/backups/{backup_id}/restore", headers=headers
        )
        assert restored.status_code == 200, restored.text
        result = await api.get(f"/api/v1/documents/{document_id}", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["name"] == "Bracket instructions"
        assert result.json()["body"] == "Mount the shelf"
        for _ in range(20):
            await asyncio.to_thread(process_one, SubjectType.DOCUMENT)
            found = await api.get(
                "/api/v1/search", params={"q": "Bracket"}, headers=headers
            )
            if found.json()["items"]:
                break
        assert found.status_code == 200, found.text
        assert [
            (item["subject_type"], item["subject_id"]) for item in found.json()["items"]
        ] == [("document", document_id)]
