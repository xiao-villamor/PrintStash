"""The vault's runtime configuration endpoint, and the two promises it makes.

The first is disclosure: `GET /config` hands back S3 and OIDC credentials' *presence*,
never their value. A regression that returned `s3_secret_key` raw would still look like
a working endpoint, so the masking is asserted here rather than assumed.

The second is the storage-migration guard. Every stored key — files, thumbnails,
documents — is resolved against `storage_backend`, `data_dir`, `thumb_dir` and the S3
namespace. Changing one of those with artifacts already on disk would leave every row
pointing at nothing, so the endpoint answers 409 `storage_migration_required` instead of
quietly remapping. There is intentionally no in-place shortcut, which makes this refusal
a data-integrity contract, not a validation nicety.

What each setting *does* once stored belongs to the module that reads it; this file
covers the endpoint's own contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import File, FileType, Model
from app.services import runtime_config
from tests.factories import build_file, build_model
from tests.integration.conftest import UserHeaders

MAKERWORLD_LEGACY_ROUTES = [
    pytest.param(
        "/api/v1/config/makerworld/login",
        {"account": "user@example.test", "password": "secret"},
        id="login",
    ),
    pytest.param(
        "/api/v1/config/makerworld/verify",
        {"login_token": "pending", "code": "123456"},
        id="verify",
    ),
    pytest.param(
        "/api/v1/config/makerworld/token", {"token": "legacy-token"}, id="token"
    ),
]
# Changing any of these remaps where every stored key resolves.
NAMESPACE_FIELDS = [
    "storage_backend",
    "data_dir",
    "thumb_dir",
    "s3_bucket",
    "s3_endpoint_url",
    "s3_region",
]


@pytest.fixture(autouse=True)
def storage_in_tmp(tmp_path: Path) -> Path:
    """Point storage at the test's tmp dir — PUT /config calls ensure_dirs()."""
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"
    return tmp_path


def _own_an_artifact(db_session: Session, data_dir: Path) -> Path:
    """Create a real blob plus the File row that owns it."""
    blob = data_dir / "model" / "v1" / "part.stl"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"owned")
    model = build_model(db_session, name="Owned", slug="owned", hash="a" * 64)
    build_file(
        db_session,
        model,
        path=str(blob),
        filename="part.stl",
        file_type=FileType.STL,
        size_bytes=5,
        sha256="b" * 64,
    )
    return blob


class TestGetConfig:
    def test_returns_the_effective_configuration(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["storage_backend"] == "local"
        assert body["backup_retention_days"] == 30
        assert body["oidc_enabled"] is False
        assert body["automatic_backups_enabled"] is False
        assert body["automatic_backup_time_utc"] == "02:00"
        assert body["manual_local_backup_enabled"] is True
        assert body["automatic_local_backup_enabled"] is True

    def test_masks_a_stored_secret(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        secret = "SECRETKEY1234567890"
        _overlay["s3_secret_key"] = secret

        body = client.get("/api/v1/config", headers=auth_headers).json()

        assert body["s3_secret_key"] == "SECR***********7890"
        assert secret not in str(body)

    def test_masks_a_short_secret_completely(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        _overlay["s3_access_key"] = "12345678"

        body = client.get("/api/v1/config", headers=auth_headers).json()

        assert body["s3_access_key"] == "********", (
            "a short key must not leak its first and last characters"
        )

    @pytest.mark.parametrize(
        ("value", "expected"),
        [pytest.param("stored", True, id="set"), pytest.param("", False, id="unset")],
    )
    def test_reports_secret_presence_without_the_value(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        value: str,
        expected: bool,
    ) -> None:
        _overlay["backup_s3_secret_key"] = value

        body = client.get("/api/v1/config", headers=auth_headers).json()

        assert body["has_backup_s3_secret_key"] is expected
        assert body["backup_s3_secret_key"] != value or value == ""

    def test_masks_typed_provider_secrets(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        runtime_config.update_storage_provider(
            db_session,
            provider="webdav",
            raw_config={
                "provider": "webdav",
                "endpoint_url": "https://dav.example.test",
                "username": "printstash",
                "password": "never-return-this",
                "root": "models",
            },
            apply_runtime=False,
        )

        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert "never-return-this" not in response.text
        assert response.json()["storage_provider_config"]["secret_fields_set"] == [
            "password"
        ]

    def test_reads_a_legacy_sftp_config_without_a_host_key(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_config_json = json.dumps(
            {
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "printstash",
                "password": "legacy-password",
                "root": "models",
            }
        )
        db_session.add(config)
        db_session.commit()

        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        provider = response.json()["storage_provider_config"]
        assert provider["host"] == "nas.example.test"
        assert provider["secret_fields_set"] == ["password"]
        assert "legacy-password" not in response.text

    def test_defaults_the_currency_to_usd(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/config", headers=auth_headers).json()

        assert body["currency"] == "USD"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/config").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get("/api/v1/config", headers=user_headers("operator"))

        assert response.status_code == 403, response.text


class TestUpdateConfig:
    def test_persists_the_automatic_backup_schedule(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={
                "automatic_backups_enabled": True,
                "automatic_backup_time_utc": "03:45",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["automatic_backups_enabled"] is True
        assert response.json()["automatic_backup_time_utc"] == "03:45"

    def test_persists_the_local_backup_selections(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={
                "manual_local_backup_enabled": False,
                "automatic_local_backup_enabled": False,
            },
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["manual_local_backup_enabled"] is False
        assert response.json()["automatic_local_backup_enabled"] is False

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("24:00", id="hour-overflow"),
            pytest.param("3:00", id="short-hour"),
        ],
    )
    def test_rejects_an_invalid_automatic_backup_time(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        value: str,
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={"automatic_backup_time_utc": value},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text

    def test_persists_the_currency(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"currency": "EUR"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["currency"] == "EUR"
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()["currency"]
            == "EUR"
        )

    @pytest.mark.parametrize(
        "currency",
        [pytest.param("EURO", id="too-long"), pytest.param("EU", id="too-short")],
    )
    def test_rejects_a_currency_that_is_not_three_characters(
        self, client: TestClient, auth_headers: dict[str, str], currency: str
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"currency": currency}, headers=auth_headers
        )

        assert response.status_code == 422, response.text

    def test_persists_the_thumbnail_width(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"model_thumbnail_width": 1280}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert (
            client.get("/api/v1/config", headers=auth_headers).json()[
                "model_thumbnail_width"
            ]
            == 1280
        )

    def test_rejects_a_thumbnail_width_outside_the_presets(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"model_thumbnail_width": 900}, headers=auth_headers
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("width", [320, 640, 1280], ids=str)
    def test_accepts_every_thumbnail_preset(
        self, client: TestClient, auth_headers: dict[str, str], width: int
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={"model_thumbnail_width": width},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text

    def test_toggles_auto_mark_known_good(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"auto_mark_known_good": False}, headers=auth_headers
        )

        assert response.json()["auto_mark_known_good"] is False

    def test_toggles_external_libraries(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={"external_libraries_enabled": True},
            headers=auth_headers,
        )

        assert response.json()["external_libraries_enabled"] is True

    def test_clears_a_retention_override_when_sent_minus_one(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.put(
            "/api/v1/config", json={"trash_retention_days": 7}, headers=auth_headers
        )

        response = client.put(
            "/api/v1/config", json={"trash_retention_days": -1}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        # -1 is the integer equivalent of sending "" for a string: it drops the DB
        # override so the environment default applies again.
        assert response.json()["trash_retention_days"] == 30

    def test_rejects_a_retention_below_minus_one(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"trash_retention_days": -2}, headers=auth_headers
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"unexpected": "ignored"}, headers=auth_headers
        )

        assert response.status_code == 422, response.text

    def test_rejects_a_storage_backend_it_does_not_implement(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"storage_backend": "ftp"}, headers=auth_headers
        )

        assert response.status_code == 400, response.text
        assert "storage_backend" in response.json()["detail"]

    def test_accepts_the_storage_backend_it_is_already_using(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"storage_backend": "local"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text

    def test_treats_clearing_the_storage_backend_as_a_remap(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config", json={"storage_backend": ""}, headers=auth_headers
        )

        # Clearing an override can expose a different environment default, so it is a
        # namespace change like any other — not a free reset.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_migration_required"

    def test_a_stored_secret_is_reported_as_present(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={"backup_s3_secret_key": "TOPSECRETVALUE123"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["has_backup_s3_secret_key"] is True
        assert "TOPSECRETVALUE123" not in str(body)

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.put("/api/v1/config", json={"currency": "EUR"}).status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.put(
            "/api/v1/config",
            json={"currency": "EUR"},
            headers=user_headers("operator"),
        )

        assert response.status_code == 403, response.text

    def test_rejects_mixed_storage_configuration_modes(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "storage_backend": "local",
                "storage_provider": "webdav",
                "storage_provider_config": {
                    "provider": "webdav",
                    "endpoint_url": "https://dav.example.test",
                    "username": "printstash",
                    "password": "secret",
                    "root": "models",
                },
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "mixed_storage_provider_input"

    def test_rejects_a_typed_provider_update_from_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.put(
            "/api/v1/config",
            headers=user_headers("provider-config-writer"),
            json={
                "storage_provider": "webdav",
                "storage_provider_config": {
                    "provider": "webdav",
                    "endpoint_url": "https://dav.example.test",
                    "username": "printstash",
                    "password": "secret",
                    "root": "models",
                },
            },
        )

        assert response.status_code == 403, response.text

    def test_updates_a_legacy_sftp_config_with_its_host_key(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_config_json = json.dumps(
            {
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "printstash",
                "root": "models",
            }
        )
        config.storage_provider_secret_json = json.dumps(
            {"password": "legacy-password"}
        )
        db_session.add(config)
        db_session.commit()

        response = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "storage_provider": "sftp",
                "storage_provider_config": {
                    "provider": "sftp",
                    "host_key": "nas.example.test ssh-ed25519 AAAA",
                },
            },
        )

        assert response.status_code == 200, response.text
        provider = response.json()["storage_provider_config"]
        assert provider["host"] == "nas.example.test"
        assert provider["host_key"] == "nas.example.test ssh-ed25519 AAAA"
        assert provider["secret_fields_set"] == ["password"]

    def test_rejects_a_new_sftp_config_without_a_host_key(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "storage_provider": "sftp",
                "storage_provider_config": {
                    "provider": "sftp",
                    "host": "nas.example.test",
                    "username": "printstash",
                    "password": "fake-password",
                    "root": "models",
                },
            },
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "sftp_host_key_required"

    def test_updates_an_unrelated_setting_with_a_legacy_sftp_config(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_config_json = json.dumps(
            {
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "printstash",
                "root": "models",
            }
        )
        config.storage_provider_secret_json = json.dumps(
            {"password": "legacy-password"}
        )
        db_session.add(config)
        db_session.commit()

        response = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={"currency": "EUR"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["currency"] == "EUR"


class TestStorageRemap:
    def test_refuses_once_an_artifact_exists(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        storage_in_tmp: Path,
    ) -> None:
        _own_an_artifact(db_session, Path(_overlay["data_dir"]))

        response = client.put(
            "/api/v1/config",
            json={"data_dir": str(storage_in_tmp / "elsewhere")},
            headers=auth_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_migration_required"

    def test_leaves_the_existing_storage_untouched(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        storage_in_tmp: Path,
    ) -> None:
        current = Path(_overlay["data_dir"])
        blob = _own_an_artifact(db_session, current)

        client.put(
            "/api/v1/config",
            json={"data_dir": str(storage_in_tmp / "elsewhere")},
            headers=auth_headers,
        )

        assert Path(_overlay["data_dir"]) == current
        assert blob.read_bytes() == b"owned"

    def test_refuses_once_the_vault_is_configured(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        storage_in_tmp: Path,
    ) -> None:
        from app.services import runtime_config

        runtime_config.mark_configured(db_session)

        response = client.put(
            "/api/v1/config",
            json={"data_dir": str(storage_in_tmp / "elsewhere")},
            headers=auth_headers,
        )

        assert response.status_code == 409, (
            "a configured vault has committed to its storage namespace"
        )

    def test_allows_a_remap_on_an_empty_unconfigured_vault(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        storage_in_tmp: Path,
    ) -> None:
        # The suite seeds sentinel Model/File rows for external print jobs; a genuinely
        # empty vault has none, which is the state a first-run operator is in.
        for table in (File, Model):
            for row in db_session.exec(select(table)).all():
                db_session.delete(row)
        db_session.commit()
        target = storage_in_tmp / "elsewhere"

        response = client.put(
            "/api/v1/config", json={"data_dir": str(target)}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert Path(response.json()["data_dir"]) == target

    def test_treats_an_unchanged_path_as_no_remap(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        current = Path(_overlay["data_dir"])
        _own_an_artifact(db_session, current)

        response = client.put(
            "/api/v1/config", json={"data_dir": str(current)}, headers=auth_headers
        )

        assert response.status_code == 200, "re-sending the same path is not a remap"

    @pytest.mark.parametrize("field", NAMESPACE_FIELDS, ids=str)
    def test_guards_every_namespace_field(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        storage_in_tmp: Path,
        field: str,
    ) -> None:
        _own_an_artifact(db_session, Path(_overlay["data_dir"]))
        value = (
            "s3"
            if field == "storage_backend"
            else str(storage_in_tmp / "elsewhere")
            if field.endswith("_dir")
            else "changed"
        )

        response = client.put(
            "/api/v1/config", json={field: value}, headers=auth_headers
        )

        assert response.status_code == 409, (
            f"{field} remaps stored keys: {response.text}"
        )


class TestMakerWorld:
    def test_reports_makerworld_as_disconnected(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/config/makerworld", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"connected": False, "updated_at": None}

    @pytest.mark.parametrize(("path", "payload"), MAKERWORLD_LEGACY_ROUTES)
    def test_answers_a_legacy_connection_attempt_with_410(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        path: str,
        payload: dict[str, str],
    ) -> None:
        response = client.post(path, json=payload, headers=auth_headers)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "makerworld_extension_required"

    def test_keeps_disconnect_working(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.delete("/api/v1/config/makerworld", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"connected": False, "updated_at": None}

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            pytest.param("get", "/api/v1/config/makerworld", id="status"),
            pytest.param("delete", "/api/v1/config/makerworld", id="disconnect"),
            pytest.param("post", "/api/v1/config/makerworld/login", id="login"),
        ],
    )
    def test_rejects_a_non_superuser(
        self,
        client: TestClient,
        user_headers: UserHeaders,
        method: str,
        path: str,
    ) -> None:
        headers = user_headers(f"operator-{method}")

        response = getattr(client, method)(path, headers=headers)

        assert response.status_code == 403, response.text


class TestStorageRootEnrollment:
    def test_requires_an_explicit_confirmation(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/config/storage-roots/enroll",
            json={"role": "data"},
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "storage_root_confirmation_required"

    def test_superuser_can_enroll_an_existing_markerless_root(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        root = Path(_overlay["data_dir"])
        root.mkdir(parents=True, exist_ok=True)

        response = client.post(
            "/api/v1/config/storage-roots/enroll",
            json={"role": "data", "confirm": True},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "enrolled": True,
            "role": "data",
            "restart_required": True,
        }
        marker = json.loads(
            (root / ".printstash-storage-root.json").read_text(encoding="utf-8")
        )
        assert marker["role"] == "data"
        assert marker["installation"]
