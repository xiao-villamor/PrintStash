"""Remote library connection profiles keep secrets encrypted and reusable."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.api.v1 import storage_connections as storage_connections_api
from app.db.models import StorageConnection
from app.services.auth import create_access_token
from app.services.library_source import LibrarySourceError
from tests.factories import build_user
from tests.integration.services.external_library._helpers import enable_feature


def _headers(user) -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


class TestStorageConnections:
    def test_remote_s3_library_reuses_a_secret_safe_profile(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "storage-profile-admin", superuser=True)
        enable_feature(db_session)

        connected = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "TrueNAS MinIO",
                "kind": "s3",
                "configuration": {
                    "provider": "s3_self_hosted",
                    "bucket": "print-library",
                    "endpoint_url": "https://minio.example.test",
                    "region": "us-east-1",
                    "addressing_style": "path",
                    "root": "models",
                },
                "secrets": {
                    "access_key": "ACCESS-SECRET",
                    "secret_key": "TOP-SECRET",
                },
            },
        )

        assert connected.status_code == 201, connected.text
        profile = connected.json()
        assert profile["secret_fields_set"] == ["access_key", "secret_key"]
        assert "TOP-SECRET" not in connected.text
        persisted = db_session.get(StorageConnection, profile["id"])
        assert persisted is not None
        raw_secret = db_session.exec(
            text("SELECT secret_json FROM storage_connections WHERE id = :id"),
            params={"id": profile["id"]},
        ).one()
        assert "TOP-SECRET" not in str(raw_secret)

        library = client.post(
            "/api/v1/libraries",
            headers=_headers(admin),
            json={
                "name": "Remote models",
                "source_kind": "s3",
                "connection_id": profile["id"],
                "source_prefix": "models",
                "scan_schedule": "0 * * * *",
            },
        )

        assert library.status_code == 201, library.text
        assert library.json()["source_kind"] == "s3"
        assert library.json()["writeback_enabled"] is False

        enrollment = client.post(
            f"/api/v1/libraries/{library.json()['id']}/root/enroll",
            headers=_headers(admin),
            json={"confirm_root_path": library.json()["root_path"]},
        )
        assert enrollment.status_code == 409
        assert enrollment.json()["detail"] == "remote_library_has_no_mounted_root"

    @pytest.mark.parametrize(
        ("kind", "configuration", "secrets", "secret_fields"),
        [
            (
                "webdav",
                {
                    "provider": "webdav",
                    "endpoint_url": "https://nas.example.test/dav",
                    "username": "reader",
                    "root": "models",
                },
                {"password": "dav-secret"},
                ["password"],
            ),
            (
                "sftp",
                {
                    "host": "nas.example.test",
                    "username": "reader",
                    "host_key": "nas.example.test ssh-ed25519 AAAATEST",
                    "root": "models",
                },
                {"password": "ssh-secret"},
                ["password"],
            ),
            (
                "gdrive",
                {"client_id": "google-client", "root": "PrintStash/models"},
                {
                    "client_secret": "google-secret",
                    "refresh_token": "google-refresh",
                },
                ["client_secret", "refresh_token"],
            ),
        ],
    )
    def test_creates_each_remote_profile_without_returning_secrets(
        self,
        client: TestClient,
        db_session: Session,
        kind: str,
        configuration: dict[str, object],
        secrets: dict[str, str],
        secret_fields: list[str],
    ) -> None:
        admin = build_user(db_session, f"{kind}-profile-admin", superuser=True)

        response = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": f"{kind.upper()} NAS",
                "kind": kind,
                "configuration": configuration,
                "secrets": secrets,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["secret_fields_set"] == secret_fields
        assert response.json()["purpose"] == "library"
        assert next(iter(secrets.values())) not in response.text

    def test_list_is_sorted_without_exposing_secrets(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "list-profile-admin", superuser=True)
        for name in ("Zulu", "Alpha"):
            response = client.post(
                "/api/v1/storage-connections",
                headers=_headers(admin),
                json={
                    "name": name,
                    "kind": "s3",
                    "configuration": {"bucket": "models", "root": "library"},
                    "secrets": {
                        "access_key": "access-secret",
                        "secret_key": "storage-secret",
                    },
                },
            )
            assert response.status_code == 201, response.text

        listed = client.get("/api/v1/storage-connections", headers=_headers(admin))

        assert listed.status_code == 200
        assert [row["name"] for row in listed.json()] == ["Alpha", "Zulu"]
        assert "storage-secret" not in listed.text

    @pytest.mark.parametrize(
        "payload, detail",
        [
            (
                {
                    "name": "Secret in config",
                    "kind": "s3",
                    "configuration": {
                        "bucket": "models",
                        "access_key": "must-not-be-here",
                    },
                    "secrets": {"secret_key": "secret"},
                },
                "storage_connection_secret_invalid",
            ),
            (
                {
                    "name": "Unknown secret",
                    "kind": "webdav",
                    "configuration": {
                        "endpoint_url": "https://nas.example.test/dav",
                        "username": "reader",
                    },
                    "secrets": {"token": "nope"},
                },
                "storage_connection_secret_invalid",
            ),
            (
                {
                    "name": "Mounted profile",
                    "kind": "mounted",
                    "configuration": {"root": "models"},
                    "secrets": {},
                },
                "storage_connection_invalid",
            ),
            (
                {
                    "name": "Invalid S3",
                    "kind": "s3",
                    "configuration": {"bucket": ""},
                    "secrets": {},
                },
                "storage_connection_invalid",
            ),
        ],
    )
    def test_rejects_unsafe_or_invalid_profiles(
        self,
        client: TestClient,
        db_session: Session,
        payload: dict[str, object],
        detail: str,
    ) -> None:
        admin = build_user(db_session, "invalid-profile-admin", superuser=True)

        response = client.post(
            "/api/v1/storage-connections", headers=_headers(admin), json=payload
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == detail

    def test_duplicate_profile_name_is_rejected(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "duplicate-profile-admin", superuser=True)
        payload = {
            "name": "Shared S3",
            "kind": "s3",
            "configuration": {"bucket": "models"},
            "secrets": {"access_key": "access", "secret_key": "secret"},
        }
        assert (
            client.post(
                "/api/v1/storage-connections", headers=_headers(admin), json=payload
            ).status_code
            == 201
        )

        duplicate = client.post(
            "/api/v1/storage-connections", headers=_headers(admin), json=payload
        )

        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "storage_connection_name_in_use"

    def test_probe_has_explicit_outcomes(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin = build_user(db_session, "probe-profile-admin", superuser=True)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Probe S3",
                "kind": "s3",
                "configuration": {"bucket": "models"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()

        source = SimpleNamespace(
            list_page=lambda *_args, **_kwargs: SimpleNamespace(entries=(1, 2))
        )
        monkeypatch.setattr(
            storage_connections_api, "source_from_connection", lambda *_a, **_k: source
        )
        success = client.post(
            f"/api/v1/storage-connections/{created['id']}/probe",
            headers=_headers(admin),
        )
        assert success.json() == {"ok": True, "sample_count": 2}

        def failed_source(*_args, **_kwargs):
            raise LibrarySourceError("library_source_list_failed")

        monkeypatch.setattr(
            storage_connections_api, "source_from_connection", failed_source
        )
        failed = client.post(
            f"/api/v1/storage-connections/{created['id']}/probe",
            headers=_headers(admin),
        )
        missing = client.post(
            "/api/v1/storage-connections/999999/probe", headers=_headers(admin)
        )

        assert failed.status_code == 409
        assert failed.json()["detail"] == "library_source_list_failed"
        assert missing.status_code == 404

    def test_probe_reports_an_unavailable_google_drive_transport(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class Unsupported(Exception):
            pass

        admin = build_user(db_session, "gdrive-probe-admin", superuser=True)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Recovery Drive",
                "kind": "gdrive",
                "purpose": "backup",
                "configuration": {
                    "client_id": "google-client",
                    "root": "PrintStash",
                },
                "secrets": {
                    "client_secret": "google-secret",
                    "refresh_token": "google-refresh",
                },
            },
        ).json()
        fake_opendal = ModuleType("opendal")

        def unregistered(_kind: str, **_options: str) -> object:
            raise Unsupported("scheme is not registered")

        fake_opendal.Operator = unregistered  # type: ignore[attr-defined]
        fake_opendal.exceptions = ModuleType("opendal.exceptions")  # type: ignore[attr-defined]
        fake_opendal.exceptions.Unsupported = Unsupported  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        response = client.post(
            f"/api/v1/storage-connections/{created['id']}/probe",
            headers=_headers(admin),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "gdrive_transport_unavailable"

    def test_backup_connection_cannot_be_attached_as_a_library(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "backup-profile-admin", superuser=True)
        enable_feature(db_session)
        connection = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Off-site copies",
                "kind": "s3",
                "purpose": "backup",
                "configuration": {"bucket": "backups", "root": "PrintStash"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        )
        assert connection.status_code == 201, connection.text

        library = client.post(
            "/api/v1/libraries",
            headers=_headers(admin),
            json={
                "name": "Wrong authority",
                "source_kind": "s3",
                "connection_id": connection.json()["id"],
            },
        )

        assert library.status_code == 409
        assert library.json()["detail"] == "storage_connection_incompatible"

    def test_backup_connection_can_be_paused_without_forgetting_credentials(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "pause-backup-profile-admin", superuser=True)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Paused copies",
                "kind": "gdrive",
                "purpose": "backup",
                "configuration": {
                    "client_id": "google-client",
                    "root": "PrintStash/backups",
                },
                "secrets": {
                    "client_secret": "google-secret",
                    "refresh_token": "google-refresh",
                },
            },
        ).json()

        paused = client.patch(
            f"/api/v1/storage-connections/{created['id']}",
            headers=_headers(admin),
            json={"enabled": False},
        )

        assert paused.status_code == 200, paused.text
        assert paused.json()["enabled"] is False
        assert paused.json()["secret_fields_set"] == [
            "client_secret",
            "refresh_token",
        ]
        assert "google-secret" not in paused.text

    def test_connection_can_be_expanded_to_both_uses(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "shared-profile-admin", superuser=True)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Shared remote storage",
                "kind": "s3",
                "configuration": {"bucket": "printstash", "root": "PrintStash"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()

        updated = client.patch(
            f"/api/v1/storage-connections/{created['id']}",
            headers=_headers(admin),
            json={"purpose": "both"},
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["purpose"] == "both"

    def test_shared_connection_can_be_attached_as_a_library(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "shared-library-admin", superuser=True)
        enable_feature(db_session)
        connection = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Shared source",
                "kind": "s3",
                "purpose": "both",
                "configuration": {"bucket": "printstash", "root": "PrintStash"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()

        library = client.post(
            "/api/v1/libraries",
            headers=_headers(admin),
            json={
                "name": "Shared catalogue",
                "source_kind": "s3",
                "connection_id": connection["id"],
                "source_prefix": "models",
            },
        )

        assert library.status_code == 201, library.text

    def test_library_use_cannot_be_removed_while_referenced(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "referenced-shared-admin", superuser=True)
        enable_feature(db_session)
        connection = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Referenced shared source",
                "kind": "s3",
                "purpose": "both",
                "configuration": {"bucket": "printstash", "root": "PrintStash"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()
        library = client.post(
            "/api/v1/libraries",
            headers=_headers(admin),
            json={
                "name": "Protected catalogue",
                "source_kind": "s3",
                "connection_id": connection["id"],
            },
        )
        assert library.status_code == 201, library.text

        blocked = client.patch(
            f"/api/v1/storage-connections/{connection['id']}",
            headers=_headers(admin),
            json={"purpose": "backup"},
        )

        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "storage_connection_in_use"

    def test_empty_connection_update_is_rejected(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "empty-profile-update-admin", superuser=True)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "No-op profile",
                "kind": "s3",
                "configuration": {"bucket": "models"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()

        response = client.patch(
            f"/api/v1/storage-connections/{created['id']}",
            headers=_headers(admin),
            json={},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "storage_connection_update_empty"

    def test_delete_is_blocked_in_use_then_succeeds_when_detached(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = build_user(db_session, "delete-profile-admin", superuser=True)
        enable_feature(db_session)
        created = client.post(
            "/api/v1/storage-connections",
            headers=_headers(admin),
            json={
                "name": "Delete S3",
                "kind": "s3",
                "configuration": {"bucket": "models"},
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        ).json()
        library = client.post(
            "/api/v1/libraries",
            headers=_headers(admin),
            json={
                "name": "Uses profile",
                "source_kind": "s3",
                "connection_id": created["id"],
                "source_prefix": "models",
            },
        )
        assert library.status_code == 201, library.text

        blocked = client.delete(
            f"/api/v1/storage-connections/{created['id']}", headers=_headers(admin)
        )
        missing = client.delete(
            "/api/v1/storage-connections/999999", headers=_headers(admin)
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "storage_connection_in_use"
        assert missing.status_code == 404

        from app.db.models import ExternalLibrary

        linked = db_session.get(ExternalLibrary, library.json()["id"])
        assert linked is not None
        db_session.delete(linked)
        db_session.commit()
        deleted = client.delete(
            f"/api/v1/storage-connections/{created['id']}", headers=_headers(admin)
        )
        assert deleted.status_code == 204
