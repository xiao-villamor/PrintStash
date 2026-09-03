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

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

import app.services.backup as backup
import app.services.storage_backend as storage_backend
from app.api.v1 import backup as backup_api
from tests.integration._backup_harness import (
    BackupEnv,
    backup_admin_headers,
    seed_model_with_blob,
    store_owned_bytes,
    user_headers_in_env,
)

POSTGRES_URL = "postgresql://printstash:secret@database/printstash"


def _assert_source_identity_conflict(
    client: TestClient,
    admin_headers: dict[str, str],
    backup_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    method: str,
    path_suffix: str,
    service_name: str,
) -> None:
    def conflict(*_args: object, **_kwargs: object) -> None:
        raise backup.BackupIdentityConflictError("backup_identity_conflict")

    monkeypatch.setattr(backup, service_name, conflict)
    response = client.request(
        method,
        f"/api/v1/backups/{backup_id}{path_suffix}",
        params={"source_ref": "exact-source"},
        headers=admin_headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "backup_identity_conflict"


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

    def test_reports_a_missing_destination_as_a_conflict(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            backup,
            "create_backup",
            lambda: (_ for _ in ()).throw(RuntimeError("backup_destination_required")),
        )

        response = client.post("/api/v1/backups", headers=admin_headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "backup_destination_required"

    def test_reports_failed_destinations_as_bad_gateway(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            backup,
            "create_backup",
            lambda: (_ for _ in ()).throw(
                RuntimeError("backup_all_destinations_failed")
            ),
        )

        response = client.post("/api/v1/backups", headers=admin_headers)

        assert response.status_code == 502, response.text
        assert response.json()["detail"] == "backup_all_destinations_failed"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.post("/api/v1/backups").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.post(
            "/api/v1/backups", headers=user_headers_in_env(backup_env)
        )

        assert response.status_code == 403, response.text


class TestUploadBackup:
    def test_registers_a_valid_archive(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
    ) -> None:
        original = backup.create_backup()
        archive = Path(original.path)
        payload = archive.read_bytes()
        filename = archive.name
        assert backup.delete_backup(original.id, source_ref=original.source_ref)

        response = client.post(
            "/api/v1/backups/upload",
            headers=admin_headers,
            files={"file": (filename, io.BytesIO(payload), "application/gzip")},
        )

        assert response.status_code == 201, response.text
        assert response.json()["backup_id"] == original.id

    def test_rejects_an_invalid_archive_without_publishing_it(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
    ) -> None:
        response = client.post(
            "/api/v1/backups/upload",
            headers=admin_headers,
            files={
                "file": (
                    "printstash-backup-20260101-000000-invalid.tar.gz",
                    b"not an archive",
                    "application/gzip",
                )
            },
        )

        assert response.status_code == 409, response.text
        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []

    def test_rejects_an_oversized_archive(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "max_upload_mb", 1)

        response = client.post(
            "/api/v1/backups/upload",
            headers=admin_headers,
            files={
                "file": (
                    "printstash-backup-20260101-000000-large.tar.gz",
                    b"x" * (1024 * 1024 + 1),
                    "application/gzip",
                )
            },
        )

        assert response.status_code == 413, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.post(
            "/api/v1/backups/upload",
            headers=user_headers_in_env(backup_env),
            files={
                "file": (
                    "printstash-backup-20260101-000000-denied.tar.gz",
                    b"archive",
                    "application/gzip",
                )
            },
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


class TestListBackupSources:
    def test_exact_sources_endpoint_exposes_replicas(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sources = [
            backup.BackupMeta(
                id="same-id",
                created_at="2020-01-01T00:00:00+00:00",
                size_bytes=10,
                storage_backend="local",
                file_count=1,
                app_version="0.13.0",
                path="/vault/one.tar.gz",
                archive_sha256="a" * 64,
                source_ref="local-ref",
                provider_ref="local-provider",
                namespace="local-vault",
                canonical=True,
                precedence=0,
            ),
            backup.BackupMeta(
                id="same-id",
                created_at="2020-01-01T00:00:00+00:00",
                size_bytes=10,
                storage_backend="s3",
                file_count=1,
                app_version="0.13.0",
                path="nexus3d-backups/one.tar.gz",
                location="s3",
                archive_sha256="a" * 64,
                source_ref="legacy-ref",
                provider_ref="legacy-provider",
                namespace="archive-bucket/nexus3d-backups/",
                canonical=False,
                precedence=1,
            ),
        ]
        monkeypatch.setattr(backup, "list_backup_sources", lambda: sources)

        response = client.get("/api/v1/backups/sources", headers=admin_headers)

        assert response.status_code == 200, response.text
        assert [item["source_ref"] for item in response.json()] == [
            "local-ref",
            "legacy-ref",
        ]
        assert response.json()[0]["canonical"] is True
        assert response.json()[1]["precedence"] == 1
        assert response.json()[1]["namespace"] == "archive-bucket/nexus3d-backups/"
        assert response.json()[0]["key"] == "/vault/one.tar.gz"
        assert response.json()[0]["provider_ref"] == "local-provider"
        assert response.json()[0]["prefix"] is None
        assert response.json()[1]["key"] == "nexus3d-backups/one.tar.gz"
        assert response.json()[1]["provider_ref"] == "legacy-provider"
        assert response.json()[1]["prefix"] == "nexus3d-backups/"


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

    def test_forwards_exact_source_ref(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ):
        observed: dict[str, str | None] = {}

        def get(_backup_id: str, *, source_ref: str | None = None):
            observed["source_ref"] = source_ref
            return a_backup

        monkeypatch.setattr(backup, "get_backup", get)
        response = client.get(
            f"/api/v1/backups/{a_backup.id}",
            params={"source_ref": "exact-source"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {"source_ref": "exact-source"}

    def test_maps_source_identity_conflict_to_http_conflict(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ) -> None:
        _assert_source_identity_conflict(
            client,
            admin_headers,
            a_backup.id,
            monkeypatch,
            method="get",
            path_suffix="",
            service_name="get_backup",
        )


class TestDownloadBackup:
    def test_serves_the_archive(
        self, client: TestClient, admin_headers: dict[str, str], a_backup
    ) -> None:
        response = client.get(
            f"/api/v1/backups/{a_backup.id}/download", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.content[:2] == b"\x1f\x8b", "a gzip archive"
        assert (
            f'filename="{Path(a_backup.path).name}"'
            in response.headers["content-disposition"]
        )

    def test_download_cleans_the_cache_after_the_response(
        self,
        client: TestClient,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache = backup_env.backup_dir / ".cloud-cache" / "download.tar.gz"
        cache.parent.mkdir(parents=True)
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(cache),
                b"cache response",
                object_kind="backup-cloud-cache",
            )
        monkeypatch.setattr(
            backup,
            "get_backup",
            lambda _backup_id, **_kwargs: backup.BackupMeta(
                id="cache",
                created_at="2026-01-01T00:00:00+00:00",
                size_bytes=len(b"cache response"),
                storage_backend="s3",
                file_count=0,
                app_version="0.13.0",
                path="printstash-backup-cache.tar.gz",
                location="s3",
                source_ref="cache-source",
            ),
        )
        monkeypatch.setattr(
            backup,
            "get_backup_archive_path",
            lambda _backup_id, **_kwargs: cache,
        )

        response = client.get(
            "/api/v1/backups/cache/download",
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.content == b"cache response"
        assert not cache.exists()

    def test_cleanup_task_is_idempotent_for_a_cloud_cache(
        self,
        backup_env: BackupEnv,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache = backup_env.backup_dir / ".cloud-cache" / "idempotent.tar.gz"
        cache.parent.mkdir(parents=True)
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(cache),
                b"cache response",
                object_kind="backup-cloud-cache",
            )
        meta = backup.BackupMeta(
            id="idempotent",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=len(b"cache response"),
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path="printstash-backup-idempotent.tar.gz",
            location="s3",
            source_ref="idempotent-source",
        )
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(
            backup, "get_backup_archive_path", lambda *_args, **_kwargs: cache
        )

        tasks = BackgroundTasks()
        response = backup_api.download_backup(
            tasks, "idempotent", source_ref=meta.source_ref
        )
        assert response.filename == meta.path
        assert response.headers["content-disposition"].endswith(
            f'filename="{meta.path}"'
        )

        asyncio.run(tasks())
        backup.cleanup_backup_cache(cache)

        assert not cache.exists()

    def test_concurrent_downloads_keep_request_unique_archives(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payloads = {name: f"payload-{name}".encode() for name in ("one", "two")}
        metas: dict[str, backup.BackupMeta] = {}
        paths: dict[str, Path] = {}
        for name, payload in payloads.items():
            cache = backup_env.backup_dir / ".cloud-cache" / f"{name}.tar.gz"
            cache.parent.mkdir(parents=True, exist_ok=True)
            with backup_env.new_session() as session:
                store_owned_bytes(
                    session,
                    storage_backend.LocalStorageBackend(),
                    str(cache),
                    payload,
                    object_kind="backup-cloud-cache",
                )
            paths[name] = cache
            metas[name] = backup.BackupMeta(
                id=name,
                created_at="2026-01-01T00:00:00+00:00",
                size_bytes=len(payload),
                storage_backend="s3",
                file_count=0,
                app_version="0.13.0",
                path=f"printstash-backup-{name}.tar.gz",
                location="s3",
                source_ref=f"{name}-source",
            )

        monkeypatch.setattr(
            backup, "get_backup", lambda backup_id, **_kwargs: metas[backup_id]
        )
        monkeypatch.setattr(
            backup,
            "get_backup_archive_path",
            lambda backup_id, **_kwargs: paths[backup_id],
        )

        def download(name: str) -> tuple[str | None, Path, BackgroundTasks]:
            tasks = BackgroundTasks()
            result = backup_api.download_backup(
                tasks, name, source_ref=f"{name}-source"
            )
            return result.filename, result.path, tasks

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(download, payloads))

        assert {filename for filename, _path, _tasks in results} == {
            meta.path for meta in metas.values()
        }
        assert {path for _filename, path, _tasks in results} == {
            path for path in paths.values()
        }
        for _filename, _path, tasks in results:
            asyncio.run(tasks())
        assert all(not path.exists() for path in paths.values())

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
        a_backup: backup.BackupMeta,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def unreadable(_backup_id: str):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(backup, "get_backup_archive_path", unreadable)

        response = client.get(
            f"/api/v1/backups/{a_backup.id}/download", headers=admin_headers
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

    def test_forwards_exact_source_ref(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ):
        observed: dict[str, str | None] = {}

        def path(_backup_id: str, *, source_ref: str | None = None) -> Path:
            observed["source_ref"] = source_ref
            return Path(a_backup.path)

        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: a_backup)
        monkeypatch.setattr(backup, "get_backup_archive_path", path)
        response = client.get(
            f"/api/v1/backups/{a_backup.id}/download",
            params={"source_ref": "exact-source"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {"source_ref": "exact-source"}

    def test_maps_source_identity_conflict_to_http_conflict(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ) -> None:
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: a_backup)
        _assert_source_identity_conflict(
            client,
            admin_headers,
            a_backup.id,
            monkeypatch,
            method="get",
            path_suffix="/download",
            service_name="get_backup_archive_path",
        )

    def test_maps_filename_identity_conflict_to_http_conflict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def conflict(*_args: object, **_kwargs: object) -> None:
            raise backup.BackupIdentityConflictError("backup_identity_conflict")

        monkeypatch.setattr(backup, "get_backup", conflict)

        with pytest.raises(HTTPException) as raised:
            backup_api.download_backup(BackgroundTasks(), "ambiguous")

        assert raised.value.status_code == 409
        assert raised.value.detail == "backup_identity_conflict"


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

    def test_forwards_exact_source_ref(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ):
        observed: dict[str, str | None] = {}

        def verify(_backup_id: str, *, source_ref: str | None = None):
            observed["source_ref"] = source_ref
            return backup.BackupVerification(
                backup_id=a_backup.id,
                valid=True,
                app_compatible=True,
                manifest_version="1",
                checked_members=1,
                findings=[],
            )

        monkeypatch.setattr(backup, "verify_backup", verify)
        response = client.post(
            f"/api/v1/backups/{a_backup.id}/verify",
            params={"source_ref": "exact-source"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {"source_ref": "exact-source"}

    def test_maps_source_identity_conflict_to_http_conflict(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ) -> None:
        _assert_source_identity_conflict(
            client,
            admin_headers,
            a_backup.id,
            monkeypatch,
            method="post",
            path_suffix="/verify",
            service_name="verify_backup",
        )


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
        def unverified(_backup_id: str, **_kwargs: object):
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

    def test_forwards_exact_source_ref(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ):
        observed: dict[str, str | bool | None] = {}

        def delete(
            _backup_id: str,
            *,
            source_ref: str | None = None,
            allow_unversioned: bool = False,
        ) -> bool:
            observed["source_ref"] = source_ref
            observed["allow_unversioned"] = allow_unversioned
            return True

        monkeypatch.setattr(backup, "delete_backup", delete)
        response = client.delete(
            f"/api/v1/backups/{a_backup.id}",
            params={"source_ref": "exact-source"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {
            "source_ref": "exact-source",
            "allow_unversioned": True,
        }

    def test_maps_source_identity_conflict_to_http_conflict(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ) -> None:
        _assert_source_identity_conflict(
            client,
            admin_headers,
            a_backup.id,
            monkeypatch,
            method="delete",
            path_suffix="",
            service_name="delete_backup",
        )


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

    def test_forwards_exact_source_ref(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ):
        observed: dict[str, str | None] = {}

        def restore(_backup_id: str, *, source_ref: str | None = None):
            observed["source_ref"] = source_ref
            return {"restored": True, "files": 1}

        monkeypatch.setattr(backup, "restore_backup", restore)
        response = client.post(
            f"/api/v1/backups/{a_backup.id}/restore",
            params={"source_ref": "exact-source"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {"source_ref": "exact-source"}

    def test_maps_source_identity_conflict_to_http_conflict(
        self, client: TestClient, admin_headers, a_backup, monkeypatch
    ) -> None:
        _assert_source_identity_conflict(
            client,
            admin_headers,
            a_backup.id,
            monkeypatch,
            method="post",
            path_suffix="/restore",
            service_name="restore_backup",
        )


class TestDiscoverUnownedS3Backups:
    def test_lists_unowned_s3_sources(
        self, client: TestClient, admin_headers, monkeypatch
    ):
        candidate = {
            "key": "nexus3d-backups/printstash-backup-legacy.tar.gz",
            "backup_id": "legacy",
            "created_at": "2020-01-01T00:00:00+00:00",
            "size_bytes": 12,
            "file_count": 1,
            "storage_backend": "local",
            "app_version": "0.13.0",
            "location": "s3",
            "namespace": "archive-bucket/nexus3d-backups/",
            "prefix": "nexus3d-backups/",
            "archive_sha256": "a" * 64,
            "source_ref": "source-ref",
        }
        monkeypatch.setattr(backup, "discover_unowned_s3_backups", lambda: [candidate])

        response = client.get("/api/v1/backups/unowned-s3", headers=admin_headers)

        assert response.status_code == 200, response.text
        assert response.json() == [candidate]


class TestAdoptS3Backup:
    def test_adopts_an_s3_source_with_exact_query_identity(
        self, client: TestClient, admin_headers, monkeypatch
    ):
        meta = backup.BackupMeta(
            id="legacy",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=12,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            path="nexus3d-backups/printstash-backup-legacy.tar.gz",
            location="s3",
            archive_sha256="a" * 64,
            source_ref="source-ref",
        )
        observed: dict[str, str] = {}

        def adopt(
            key: str, *, source_ref: str, expected_archive_sha256: str
        ) -> backup.BackupMeta:
            observed.update(
                key=key,
                source_ref=source_ref,
                expected_archive_sha256=expected_archive_sha256,
            )
            return meta

        monkeypatch.setattr(backup, "adopt_s3_backup", adopt)
        params = {
            "key": meta.path,
            "source_ref": "source-ref",
            "expected_archive_sha256": "a" * 64,
        }

        response = client.post(
            "/api/v1/backups/adopt-s3", params=params, headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert observed == {
            "key": meta.path,
            "source_ref": "source-ref",
            "expected_archive_sha256": "a" * 64,
        }
        assert response.json()["source_ref"] == "source-ref"
        assert response.json()["archive_sha256"] == "a" * 64

    def test_reports_s3_adoption_conflict(
        self, client: TestClient, admin_headers, monkeypatch
    ):
        monkeypatch.setattr(
            backup,
            "adopt_s3_backup",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("backup_source_ref_mismatch")
            ),
        )

        response = client.post(
            "/api/v1/backups/adopt-s3",
            params={
                "key": "nexus3d-backups/printstash-backup-legacy.tar.gz",
                "source_ref": "wrong",
                "expected_archive_sha256": "a" * 64,
            },
            headers=admin_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "backup_source_ref_mismatch"


class TestDiscoverUnownedRemoteBackups:
    def test_lists_opendal_candidates(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        candidate = {
            "connection_id": 7,
            "connection_name": "Recovery Drive",
            "provider": "gdrive",
            "key": "gdrive/PrintStash/printstash-backups/printstash-backup-old.tar.gz",
            "backup_id": "old",
            "created_at": "2026-01-01T00:00:00+00:00",
            "size_bytes": 12,
            "file_count": 1,
            "storage_backend": "local",
            "app_version": "0.13.0",
            "location": "opendal:gdrive",
            "namespace": "gdrive/PrintStash",
            "prefix": "gdrive/PrintStash/printstash-backups",
            "archive_sha256": "a" * 64,
            "source_ref": "source-ref",
        }
        monkeypatch.setattr(
            backup, "discover_unowned_opendal_backups", lambda: [candidate]
        )

        response = client.get("/api/v1/backups/unowned-remote", headers=admin_headers)

        assert response.status_code == 200, response.text
        assert response.json() == [candidate]

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.get(
            "/api/v1/backups/unowned-remote",
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text


class TestAdoptRemoteBackup:
    def test_forwards_the_exact_opendal_candidate(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key = "gdrive/PrintStash/printstash-backups/printstash-backup-old.tar.gz"
        observed: dict[str, object] = {}

        def adopt(
            connection_id: int,
            candidate_key: str,
            *,
            source_ref: str,
            expected_archive_sha256: str,
        ) -> backup.BackupMeta:
            observed.update(
                connection_id=connection_id,
                key=candidate_key,
                source_ref=source_ref,
                expected_archive_sha256=expected_archive_sha256,
            )
            return backup.BackupMeta(
                id="old",
                created_at="2026-01-01T00:00:00+00:00",
                size_bytes=12,
                storage_backend="local",
                file_count=1,
                app_version="0.13.0",
                path=key,
                location="opendal:gdrive",
                archive_sha256="a" * 64,
                source_ref="source-ref",
                provider_ref="provider-ref",
                namespace="gdrive/PrintStash",
            )

        monkeypatch.setattr(backup, "adopt_opendal_backup", adopt)

        response = client.post(
            "/api/v1/backups/adopt-remote",
            params={
                "connection_id": 7,
                "key": key,
                "source_ref": "source-ref",
                "expected_archive_sha256": "a" * 64,
            },
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert observed == {
            "connection_id": 7,
            "key": key,
            "source_ref": "source-ref",
            "expected_archive_sha256": "a" * 64,
        }

    def test_rejects_a_non_superuser(
        self, client: TestClient, backup_env: BackupEnv
    ) -> None:
        response = client.post(
            "/api/v1/backups/adopt-remote",
            params={
                "connection_id": 7,
                "key": "gdrive/PrintStash/printstash-backups/printstash-backup-old.tar.gz",
                "source_ref": "source-ref",
                "expected_archive_sha256": "a" * 64,
            },
            headers=user_headers_in_env(backup_env),
        )

        assert response.status_code == 403, response.text
