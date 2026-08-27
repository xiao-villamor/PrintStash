"""Backup HTTP operations preserve recoverability, ownership, and admin scope."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import Model, User
from app.services import backup
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import get_backend
from tests.integration.services.backup._backup_shared import (
    BackupEnv,
    _auth_headers,
    _read_model_names,
    _seed_model_with_blob,
)


def _regular_headers(env: BackupEnv) -> dict[str, str]:
    with env.new_session() as session:
        user = User(
            username="backup-regular",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _rewrite_manifest(meta: backup.BackupMeta, mutate) -> None:
    path = Path(meta.path)
    contents: dict[str, bytes] = {}
    with (
        gzip.open(path, "rb") as stream,
        tarfile.open(fileobj=stream, mode="r:") as archive,
    ):
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            if member.isfile() and extracted is not None:
                contents[member.name] = extracted.read()
    manifest = json.loads(contents["manifest.json"])
    mutate(manifest, contents)
    contents["manifest.json"] = json.dumps(manifest).encode()
    with (
        gzip.open(path, "wb") as stream,
        tarfile.open(fileobj=stream, mode="w:") as archive,
    ):
        for name, data in contents.items():
            member = tarfile.TarInfo(name=name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


class TestDatabaseCapabilities:
    def test_reports_file_backed_sqlite_backup_support(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/backups/capabilities/database",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "database_backend": "sqlite",
            "create_supported": True,
            "restore_supported": True,
        }

    def test_reports_postgresql_backup_as_unsupported(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _overlay["db_url"] = "postgresql://user:fake@db.example.test/printstash"

        response = client.get(
            "/api/v1/backups/capabilities/database",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "database_backend": "postgresql",
            "create_supported": False,
            "restore_supported": False,
        }

    def test_denies_a_non_superuser_from_reading_backup_capabilities(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/backups/capabilities/database",
            headers=_regular_headers(backup_env),
        )

        assert response.status_code == 403, response.text

    def test_denies_an_unauthenticated_caller_from_reading_backup_capabilities(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get("/api/v1/backups/capabilities/database")

        assert response.status_code == 401, response.text


class TestListBackups:
    def test_lists_available_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        first = backup.create_backup()
        second = backup.create_backup()

        response = client.get("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 200, response.text
        assert {row["backup_id"] for row in response.json()} == {
            first.id,
            second.id,
        }

    def test_returns_an_empty_backup_list(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_omits_local_paths_and_credentials_from_backup_listings(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        secret = "backup-list-secret"
        _overlay["backup_s3_secret_key"] = secret
        meta = backup.create_backup()

        response = client.get("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 200, response.text
        assert "path" not in response.json()[0]
        assert meta.path not in response.text
        assert secret not in response.text

    def test_denies_a_non_superuser_from_listing_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get("/api/v1/backups", headers=_regular_headers(backup_env))

        assert response.status_code == 403, response.text


class TestCreateBackup:
    def test_creates_a_backup_job(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.post("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 202, response.text
        assert response.json()["backup_id"]
        assert Path(response.json()["location"]).name == "local"

    def test_captures_database_and_owned_blobs_in_a_created_archive(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _, key = _seed_model_with_blob(
            backup_env, name="Router backup", content=b"solid router backup\n"
        )

        response = client.post("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 202, response.text
        meta = backup.get_backup(response.json()["backup_id"])
        assert meta is not None
        with tarfile.open(meta.path, mode="r:gz") as archive:
            names = archive.getnames()
            manifest_stream = archive.extractfile("manifest.json")
            assert manifest_stream is not None
            manifest = json.loads(manifest_stream.read())
        assert "db.sqlite3" in names
        assert [entry["key"] for entry in manifest["files"]] == [key]

    def test_fails_closed_when_an_owned_blob_is_missing(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _, key = _seed_model_with_blob(
            backup_env, name="Missing router blob", content=b"gone"
        )
        path = get_backend().direct_path(key)
        assert path is not None
        path.unlink()

        failure_client = TestClient(client.app, raise_server_exceptions=False)
        try:
            response = failure_client.post(
                "/api/v1/backups", headers=_auth_headers(backup_env)
            )
        finally:
            failure_client.close()

        assert response.status_code == 500, response.text
        assert backup.list_backups() == []

    def test_rejects_backup_creation_on_unsupported_database_mode(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _overlay["db_url"] = "postgresql://user:fake@db.example.test/printstash"

        response = client.post("/api/v1/backups", headers=_auth_headers(backup_env))

        assert response.status_code == 501, response.text
        assert backup.list_backups() == []

    def test_denies_a_non_superuser_from_creating_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.post("/api/v1/backups", headers=_regular_headers(backup_env))

        assert response.status_code == 403, response.text
        assert backup.list_backups() == []


class TestGetBackup:
    def test_returns_backup_metadata(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.get(
            f"/api/v1/backups/{meta.id}", headers=_auth_headers(backup_env)
        )

        assert response.status_code == 200, response.text
        assert response.json()["backup_id"] == meta.id
        assert response.json()["size_bytes"] == meta.size_bytes

    def test_returns_not_found_for_missing_backup_metadata(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/backups/missing", headers=_auth_headers(backup_env)
        )

        assert response.status_code == 404, response.text

    @pytest.mark.parametrize(
        "backup_id",
        [
            pytest.param("..", id="parent"),
            pytest.param("%2E%2E", id="encoded-parent"),
            pytest.param("%00", id="nul"),
        ],
    )
    def test_rejects_an_unsafe_backup_identifier(
        self, backup_env: BackupEnv, client: TestClient, backup_id: str
    ) -> None:
        response = client.get(
            f"/api/v1/backups/{backup_id}", headers=_auth_headers(backup_env)
        )

        assert response.status_code in {404, 422}, response.text

    def test_denies_a_non_superuser_from_reading_backup_metadata(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.get(
            f"/api/v1/backups/{meta.id}", headers=_regular_headers(backup_env)
        )

        assert response.status_code == 403, response.text


class TestDownloadBackup:
    def test_downloads_a_backup_archive(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()
        expected = Path(meta.path).read_bytes()

        response = client.get(
            f"/api/v1/backups/{meta.id}/download",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.content == expected
        assert "attachment" in response.headers["content-disposition"]

    def test_returns_not_found_when_downloading_a_missing_backup(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/backups/missing/download",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 404, response.text

    def test_rejects_traversal_during_backup_download(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/backups/%2E%2E/download",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code in {404, 422}, response.text

    def test_denies_a_non_superuser_from_downloading_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.get(
            f"/api/v1/backups/{meta.id}/download",
            headers=_regular_headers(backup_env),
        )

        assert response.status_code == 403, response.text


class TestVerifyBackup:
    def test_verifies_a_valid_backup_archive(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.post(
            f"/api/v1/backups/{meta.id}/verify",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.json()["valid"] is True
        assert response.json()["app_compatible"] is True

    def test_rejects_manifest_membership_mismatch(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _seed_model_with_blob(backup_env, name="Mismatch", content=b"mismatch")
        meta = backup.create_backup()

        def mutate(manifest: dict, contents: dict[str, bytes]) -> None:
            manifest["files"].append({"arc": "files/missing.stl", "size": 4})

        _rewrite_manifest(meta, mutate)

        response = client.post(
            f"/api/v1/backups/{meta.id}/verify",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.json()["valid"] is False
        assert any(
            finding["code"] == "backup_member_missing"
            for finding in response.json()["findings"]
        )

    def test_rejects_an_incompatible_backup_version(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        def mutate(manifest: dict, contents: dict[str, bytes]) -> None:
            manifest["version"] = "999"

        _rewrite_manifest(meta, mutate)

        response = client.post(
            f"/api/v1/backups/{meta.id}/verify",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 200, response.text
        assert response.json()["valid"] is False
        assert response.json()["app_compatible"] is False

    def test_returns_not_found_when_verifying_a_missing_backup(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/backups/missing/verify",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 404, response.text

    def test_denies_a_non_superuser_from_verifying_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.post(
            f"/api/v1/backups/{meta.id}/verify",
            headers=_regular_headers(backup_env),
        )

        assert response.status_code == 403, response.text


class TestRestoreBackup:
    def test_restores_a_valid_backup(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        auth_headers = _auth_headers(backup_env)
        _, key = _seed_model_with_blob(
            backup_env, name="Restored model", content=b"restore"
        )
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            for row in session.exec(select(Model)).all():
                if row.name == "Restored model":
                    session.delete(row)
            session.commit()
        blob_path = get_backend().direct_path(key)
        assert blob_path is not None
        blob_path.unlink()

        response = client.post(
            f"/api/v1/backups/{meta.id}/restore",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert "Restored model" in _read_model_names(backup_env)

    def test_rejects_restore_on_unsupported_database_mode(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        _overlay["db_url"] = "postgresql://user:fake@db.example.test/printstash"

        response = client.post(
            "/api/v1/backups/missing/restore",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 501, response.text

    def test_returns_not_found_when_restoring_a_missing_backup(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/backups/missing/restore",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code == 404, response.text

    def test_denies_a_non_superuser_from_restoring_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/backups/missing/restore",
            headers=_regular_headers(backup_env),
        )

        assert response.status_code == 403, response.text


class TestDeleteBackup:
    def test_deletes_a_backup(self, backup_env: BackupEnv, client: TestClient) -> None:
        meta = backup.create_backup()

        response = client.delete(
            f"/api/v1/backups/{meta.id}", headers=_auth_headers(backup_env)
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"backup_id": meta.id, "deleted": True}
        assert backup.get_backup(meta.id) is None

    def test_returns_not_found_when_deleting_a_missing_backup(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.delete(
            "/api/v1/backups/missing", headers=_auth_headers(backup_env)
        )

        assert response.status_code == 404, response.text

    def test_rejects_traversal_during_backup_deletion(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        response = client.delete(
            "/api/v1/backups/%2E%2E",
            headers=_auth_headers(backup_env),
        )

        assert response.status_code in {404, 422}, response.text

    def test_denies_a_non_superuser_from_deleting_backups(
        self, backup_env: BackupEnv, client: TestClient
    ) -> None:
        meta = backup.create_backup()

        response = client.delete(
            f"/api/v1/backups/{meta.id}", headers=_regular_headers(backup_env)
        )

        assert response.status_code == 403, response.text
        assert backup.get_backup(meta.id) is not None
