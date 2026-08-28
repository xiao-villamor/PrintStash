"""The backup endpoints an operator's disaster recovery depends on.

Two things make this router worth its own tests rather than trusting the service's.
First, it is the surface that tells an operator what the vault *can* do: on PostgreSQL
there is no supported database backup, and the endpoint must say so — 501 before
creating anything and 501 before restoring anything, so nobody is handed an archive
that cannot be restored. Second, a restore is the most destructive operation in the
product, so it refuses while ingestion work is in flight rather than replacing the
database underneath it.

Everything here is superuser-only. The archive format, the round-trip, and what a
restore actually recovers live in `integration/services/test_backup.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.backup as backup
from tests.integration._backup_harness import (
    BackupEnv,
    backup_admin_headers,
    seed_model_with_blob,
    user_headers_in_env,
)

POSTGRES_URL = "postgresql://printstash:secret@database/printstash"


@pytest.fixture
def admin_headers(backup_env: BackupEnv) -> dict[str, str]:
    return backup_admin_headers(backup_env)


@pytest.fixture
def a_backup(backup_env: BackupEnv):
    """One real archive of a vault holding one model."""
    seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
    return backup.create_backup()


class TestCreateBackup:
    def test_returns_the_new_backups_metadata(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"x")

        response = client.post("/api/v1/backups", headers=admin_headers)

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["backup_id"]
        assert body["file_count"] == 1
        assert body["location"] == "local"

    def test_writes_the_archive(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"x")

        client.post("/api/v1/backups", headers=admin_headers)

        assert list(backup_env.backup_dir.glob("*.tar.gz"))

    def test_refuses_on_a_database_it_cannot_back_up(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "db_url", POSTGRES_URL)

        response = client.post("/api/v1/backups", headers=admin_headers)

        assert response.status_code == 501, response.text
        assert response.json()["detail"] == "database_backup_not_supported"

    def test_writes_nothing_when_it_refuses(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "db_url", POSTGRES_URL)

        client.post("/api/v1/backups", headers=admin_headers)

        # A half-written archive would look restorable and is not.
        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.post("/api/v1/backups").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.post(
            "/api/v1/backups", headers=user_headers_in_env(backup_env)
        )

        assert response.status_code == 403, response.text


class TestListBackups:
    def test_lists_the_stored_backups(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.get("/api/v1/backups", headers=admin_headers)

        assert response.status_code == 200, response.text
        assert a_backup.id in {row["backup_id"] for row in response.json()}

    def test_returns_an_empty_list_with_no_backups(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        assert client.get("/api/v1/backups", headers=admin_headers).json() == []

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.get(
            "/api/v1/backups", headers=user_headers_in_env(backup_env)
        )

        assert response.status_code == 403, response.text


class TestDatabaseCapabilities:
    def test_reports_sqlite_as_fully_supported(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/backups/capabilities/database", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "database_backend": "sqlite",
            "create_supported": True,
            "restore_supported": True,
        }

    def test_reports_postgresql_as_unsupported(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "db_url", POSTGRES_URL)

        response = client.get(
            "/api/v1/backups/capabilities/database", headers=admin_headers
        )

        assert response.json() == {
            "database_backend": "postgresql",
            "create_supported": False,
            "restore_supported": False,
        }

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.get(
            "/api/v1/backups/capabilities/database",
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text


class TestGetBackup:
    def test_returns_the_backups_metadata(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.get(f"/api/v1/backups/{a_backup.id}", headers=admin_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["backup_id"] == a_backup.id
        assert body["file_count"] == a_backup.file_count
        assert body["location"] == "local"

    def test_reports_an_unknown_backup_as_not_found(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/backups/does-not-exist", headers=admin_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "backup_not_found"

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv, a_backup
    ) -> None:
        response = client.get(
            f"/api/v1/backups/{a_backup.id}", headers=user_headers_in_env(backup_env)
        )

        assert response.status_code == 403, response.text


class TestDownloadBackup:
    def test_serves_the_archive(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.get(
            f"/api/v1/backups/{a_backup.id}/download", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.content[:2] == b"\x1f\x8b", "a gzip archive"

    def test_reports_an_unknown_backup_as_not_found(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/backups/does-not-exist/download", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "backup_not_found"

    def test_surfaces_an_unexpected_failure(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def unreadable(_backup_id: str):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(backup, "get_backup_archive_path", unreadable)

        response = client.get(
            "/api/v1/backups/whatever/download", headers=admin_headers
        )

        assert response.status_code == 500, response.text
        assert "disk on fire" in response.json()["detail"]

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv, a_backup
    ) -> None:
        response = client.get(
            f"/api/v1/backups/{a_backup.id}/download",
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text


class TestVerifyBackup:
    def test_reports_a_sound_archive(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.post(
            f"/api/v1/backups/{a_backup.id}/verify", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["valid"] is True
        assert body["app_compatible"] is True
        assert body["findings"] == []

    def test_reports_an_unknown_backup_as_not_found(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/backups/does-not-exist/verify", headers=admin_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv, a_backup
    ) -> None:
        response = client.post(
            f"/api/v1/backups/{a_backup.id}/verify",
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text


class TestDeleteBackup:
    def test_reports_the_deletion(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.delete(
            f"/api/v1/backups/{a_backup.id}", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"backup_id": a_backup.id, "deleted": True}

    def test_removes_the_archive(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        client.delete(f"/api/v1/backups/{a_backup.id}", headers=admin_headers)

        assert not Path(a_backup.path).exists()

    def test_reports_an_unknown_backup_as_not_found(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.delete(
            "/api/v1/backups/does-not-exist", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "backup_not_found"

    def test_refuses_when_storage_ownership_is_unverified(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        a_backup,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Deleting a blob the vault cannot prove it owns is how another
        # application's data gets destroyed; the service refuses and the router
        # must surface that rather than reporting success.
        def unverified(_backup_id: str):
            raise backup.BackupOwnershipError("cannot verify ownership")

        monkeypatch.setattr(backup, "delete_backup", unverified)

        response = client.delete(
            f"/api/v1/backups/{a_backup.id}", headers=admin_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "backup_storage_ownership_unverified"

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv, a_backup
    ) -> None:
        response = client.delete(
            f"/api/v1/backups/{a_backup.id}", headers=user_headers_in_env(backup_env)
        )

        assert response.status_code == 403, response.text


class TestRestoreBackup:
    def test_returns_what_the_restore_reports(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        a_backup,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The restore itself is covered end to end in the service tests; here the
        # router's job is only to hand back what it was given.
        monkeypatch.setattr(
            backup, "restore_backup", lambda _id: {"restored": True, "files": 1}
        )

        response = client.post(
            f"/api/v1/backups/{a_backup.id}/restore", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"restored": True, "files": 1}

    def test_refuses_when_storage_ownership_is_unverified(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        a_backup,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def unverified(_backup_id: str):
            raise backup.BackupOwnershipError("cannot verify ownership")

        monkeypatch.setattr(backup, "restore_backup", unverified)

        response = client.post(
            f"/api/v1/backups/{a_backup.id}/restore", headers=admin_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "backup_storage_ownership_unverified"

    def test_reports_an_unknown_backup_as_not_found(
        self, client: TestClient, backup_env: BackupEnv, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/backups/does-not-exist/restore", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "backup_not_found"

    def test_refuses_while_ingestion_work_is_in_flight(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        from app.services.jobs import registry

        job_id = registry.create()
        registry.update(job_id, state="running")
        try:
            response = client.post(
                f"/api/v1/backups/{a_backup.id}/restore", headers=admin_headers
            )
        finally:
            registry.update(job_id, state="completed")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "1 ingestion job(s) and 0 staging lease(s) active"
        )

    def test_refuses_on_a_database_it_cannot_restore(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "db_url", POSTGRES_URL)

        response = client.post("/api/v1/backups/any-id/restore", headers=admin_headers)

        assert response.status_code == 501, response.text
        assert response.json()["detail"] == "database_backup_not_supported"

    def test_surfaces_an_unexpected_failure(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        a_backup,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing(_backup_id: str):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(backup, "restore_backup", failing)

        response = client.post(
            f"/api/v1/backups/{a_backup.id}/restore", headers=admin_headers
        )

        assert response.status_code == 500, response.text
        assert "kaboom" in response.json()["detail"]

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv, a_backup
    ) -> None:
        response = client.post(
            f"/api/v1/backups/{a_backup.id}/restore",
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text
