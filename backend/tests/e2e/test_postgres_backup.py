"""A PostgreSQL installation can back up and restore through the public API."""

from uuid import uuid4

import pytest
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
from tests.containers import postgres_url
from tests.e2e._backup_helpers import setup_and_login
from tests.e2e._jobs import create_backup
from tests.search_projection import drain_search


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
        original_version = created.json()["edit_version"]
        before_restore = await api.get(
            "/api/v1/models/browse/revision", headers=headers
        )
        assert before_restore.status_code == 200, before_restore.text
        # A backup is a Job: the route answers 202, the Job builds the archive.
        backup_id = (await create_backup(api, headers))["backup_id"]
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
        assert result.json()["edit_version"] == original_version
        after_restore = await api.get("/api/v1/models/browse/revision", headers=headers)
        assert after_restore.status_code == 200, after_restore.text
        assert (
            after_restore.json()["browse_revision"].split(":")[0]
            != before_restore.json()["browse_revision"].split(":")[0]
        )
        assert (
            after_restore.json()["authorization_revision"].split(":")[0]
            == after_restore.json()["browse_revision"].split(":")[0]
        )
        # ASGITransport does not start the background projection worker.
        drain_search(postgres_e2e_db)
        found = await api.get(
            "/api/v1/search", params={"q": "Bracket"}, headers=headers
        )
        assert found.status_code == 200, found.text
        assert [
            (item["subject_type"], item["subject_id"]) for item in found.json()["items"]
        ] == [("document", document_id)]

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_rejects_document_draft_from_restored_history(
        self, api, tmp_path, postgres_e2e_db
    ):
        headers = await setup_and_login(api, tmp_path)
        created = await api.post(
            "/api/v1/documents",
            headers=headers,
            json={"name": "Before restore", "body": "Original"},
        )
        assert created.status_code == 201, created.text
        path = f"/api/v1/documents/{created.json()['id']}"
        before = await api.get(path, headers=headers)
        assert before.status_code == 200, before.text
        backup_id = (await create_backup(api, headers))["backup_id"]
        restored = await api.post(
            f"/api/v1/backups/{backup_id}/restore", headers=headers
        )
        assert restored.status_code == 200, restored.text
        current = await api.get(path, headers=headers)
        assert current.json()["edit_version"] == before.json()["edit_version"]
        assert current.json()["edit_epoch"] != before.json()["edit_epoch"]
        rejected = await api.put(
            path,
            headers={**headers, "If-Match": before.headers["etag"]},
            json={"name": "Obsolete draft"},
        )
        assert rejected.status_code == 412, rejected.text
        assert (await api.get(path, headers=headers)).json()["name"] == "Before restore"
