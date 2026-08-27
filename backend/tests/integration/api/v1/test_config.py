"""Currency setting round-trips through the config API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import File, FileType, Model, SystemConfig, User
from app.services.auth import create_access_token, hash_password


def _configure_storage(tmp_path: Path) -> None:
    # PUT /config calls ensure_dirs(); point storage at the test's tmp dir so it
    # doesn't try to mkdir the real /data root.
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"


def _regular_headers(session: Session, username: str) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


class TestGetConfig:
    def test_returns_current_vault_configuration(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert set(response.json()) == {
            "storage_backend",
            "data_dir",
            "thumb_dir",
            "s3_bucket",
            "s3_endpoint_url",
            "s3_region",
            "s3_access_key",
            "s3_secret_key",
            "has_s3_access_key",
            "has_s3_secret_key",
            "backup_retention_days",
            "trash_retention_days",
            "backup_s3_bucket",
            "backup_s3_endpoint_url",
            "backup_s3_region",
            "backup_s3_access_key",
            "backup_s3_secret_key",
            "has_backup_s3_access_key",
            "has_backup_s3_secret_key",
            "has_backup_s3",
            "auto_mark_known_good",
            "external_libraries_enabled",
            "currency",
            "model_thumbnail_width",
            "oidc_enabled",
            "oidc_issuer_url",
            "oidc_client_id",
            "has_oidc_client_secret",
            "oidc_scopes",
            "oidc_username_claim",
            "oidc_groups_claim",
            "oidc_admin_groups",
            "oidc_display_name",
            "oidc_redirect_uri",
            "oidc_allow_insecure_http",
            "storage_tier",
            "storage_capabilities",
            "storage_warnings",
            "storage_probe_diagnostics",
            "storage_unverified_acknowledged",
            "storage_provider",
            "storage_provider_config",
        }
        body = response.json()
        assert body["storage_tier"] == "verified"
        assert body["storage_capabilities"]["object_identity"] == "inode"
        assert body["storage_warnings"] == []

    def test_returns_credential_presence_flags(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        _configure_storage(tmp_path)
        update = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "s3_access_key": "asset-access",
                "s3_secret_key": "asset-secret",
                "backup_s3_bucket": "backups",
                "backup_s3_access_key": "backup-access",
                "backup_s3_secret_key": "backup-secret",
                "oidc_client_secret": "oidc-secret",
            },
        )
        assert update.status_code == 200, update.text

        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["has_s3_access_key"] is True
        assert body["has_s3_secret_key"] is True
        assert body["has_backup_s3"] is True
        assert body["has_backup_s3_access_key"] is True
        assert body["has_backup_s3_secret_key"] is True
        assert body["has_oidc_client_secret"] is True

    def test_redacts_storage_backup_and_oidc_secrets(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        _configure_storage(tmp_path)
        secrets = (
            "asset-access-value",
            "asset-secret-value",
            "backup-access-value",
            "backup-secret-value",
            "oidc-secret-value",
        )
        update = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "s3_access_key": secrets[0],
                "s3_secret_key": secrets[1],
                "backup_s3_access_key": secrets[2],
                "backup_s3_secret_key": secrets[3],
                "oidc_client_secret": secrets[4],
            },
        )
        assert update.status_code == 200, update.text

        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert all(secret not in response.text for secret in secrets)

    def test_denies_a_non_superuser_from_reading_configuration(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers = _regular_headers(db_session, "config-reader")

        response = client.get("/api/v1/config", headers=headers)

        assert response.status_code == 403, response.text

    def test_denies_an_unauthenticated_caller_from_reading_configuration(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/config")

        assert response.status_code == 401, response.text


def test_currency_defaults_to_usd(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/config", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "USD"


def test_currency_can_be_updated(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put("/api/v1/config", json={"currency": "EUR"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "EUR"

    # Persisted across reads.
    assert (
        client.get("/api/v1/config", headers=auth_headers).json()["currency"] == "EUR"
    )


def test_currency_rejects_bad_length(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put("/api/v1/config", json={"currency": "EURO"}, headers=auth_headers)
    assert resp.status_code == 422


def test_model_thumbnail_quality_round_trips_and_rejects_unknown_presets(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    response = client.put(
        "/api/v1/config",
        json={"model_thumbnail_width": 1280},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["model_thumbnail_width"] == 1280
    assert (
        client.get("/api/v1/config", headers=auth_headers).json()[
            "model_thumbnail_width"
        ]
        == 1280
    )

    invalid = client.put(
        "/api/v1/config",
        json={"model_thumbnail_width": 900},
        headers=auth_headers,
    )
    assert invalid.status_code == 422


def test_update_rejects_invalid_storage_backend(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put(
        "/api/v1/config", json={"storage_backend": "ftp"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "storage_backend" in resp.json()["detail"]


def test_update_rejects_storage_remap_once_artifacts_exist(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    current = Path(_overlay["data_dir"])
    current.mkdir(parents=True)
    blob = current / "model" / "v1" / "part.stl"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"owned")
    model = Model(name="Owned", slug="owned", hash="a" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    db_session.add(
        File(
            model_id=model.id,
            path=str(blob),
            original_filename="part.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=5,
            sha256="b" * 64,
        )
    )
    db_session.commit()

    response = client.put(
        "/api/v1/config",
        json={"data_dir": str(tmp_path / "other")},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "storage_migration_required"
    assert Path(_overlay["data_dir"]) == current
    assert blob.read_bytes() == b"owned"


def test_update_toggles_auto_mark_known_good_and_external_libraries(
    client: TestClient, auth_headers: dict[str, str], tmp_path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put(
        "/api/v1/config",
        json={"auto_mark_known_good": False, "external_libraries_enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auto_mark_known_good"] is False
    assert body["external_libraries_enabled"] is True


def test_applies_a_partial_configuration_update(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    initial = client.get("/api/v1/config", headers=auth_headers).json()

    response = client.put(
        "/api/v1/config",
        json={"currency": "EUR"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["currency"] == "EUR"
    assert response.json()["backup_retention_days"] == initial["backup_retention_days"]
    assert response.json()["trash_retention_days"] == initial["trash_retention_days"]


def test_persists_backup_target_configuration(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)

    response = client.put(
        "/api/v1/config",
        json={
            "backup_retention_days": 14,
            "backup_s3_bucket": "vault-backups",
            "backup_s3_endpoint_url": "https://backups.example.test",
            "backup_s3_region": "auto",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    reread = client.get("/api/v1/config", headers=auth_headers).json()
    assert reread["backup_retention_days"] == 14
    assert reread["backup_s3_bucket"] == "vault-backups"
    assert reread["backup_s3_endpoint_url"] == "https://backups.example.test"


def test_protects_saved_oidc_client_secret(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    secret = "oidc-client-secret-value"

    response = client.put(
        "/api/v1/config",
        json={"oidc_client_secret": secret},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    raw = (
        db_session.connection()
        .exec_driver_sql("SELECT oidc_client_secret FROM system_config WHERE id = 1")
        .scalar_one()
    )
    assert raw != secret
    assert secret not in response.text


def test_preserves_omitted_secrets_during_update(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    first = client.put(
        "/api/v1/config",
        json={"s3_secret_key": "preserved-secret"},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text

    response = client.put(
        "/api/v1/config",
        json={"currency": "EUR"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["has_s3_secret_key"] is True


def test_clears_a_secret_only_through_the_documented_clear_value(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    first = client.put(
        "/api/v1/config",
        json={"s3_secret_key": "clear-me"},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text

    response = client.put(
        "/api/v1/config",
        json={"s3_secret_key": ""},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["has_s3_secret_key"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("backup_retention_days", -2, id="backup"),
        pytest.param("trash_retention_days", -2, id="trash"),
    ],
)
def test_validates_retention_lower_boundaries(
    client: TestClient,
    auth_headers: dict[str, str],
    field: str,
    value: int,
) -> None:
    response = client.put(
        "/api/v1/config",
        json={field: value},
        headers=auth_headers,
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("oidc_issuer_url", "x" * 513, id="issuer"),
        pytest.param("oidc_client_id", "x" * 256, id="client-id"),
        pytest.param("oidc_client_secret", "x" * 2049, id="client-secret"),
        pytest.param("oidc_scopes", "x" * 513, id="scopes"),
        pytest.param("oidc_username_claim", "x" * 129, id="username-claim"),
        pytest.param("oidc_groups_claim", "x" * 129, id="groups-claim"),
        pytest.param("oidc_admin_groups", "x" * 1025, id="admin-groups"),
        pytest.param("oidc_display_name", "x" * 129, id="display-name"),
        pytest.param("oidc_redirect_uri", "x" * 1025, id="redirect-uri"),
    ],
)
def test_validates_oidc_field_boundaries(
    client: TestClient,
    auth_headers: dict[str, str],
    field: str,
    value: str,
) -> None:
    response = client.put(
        "/api/v1/config",
        json={field: value},
        headers=auth_headers,
    )

    assert response.status_code == 422, response.text


def test_denies_a_non_superuser_from_updating_configuration(
    client: TestClient, db_session: Session
) -> None:
    headers = _regular_headers(db_session, "config-writer")

    response = client.put(
        "/api/v1/config",
        json={"currency": "EUR"},
        headers=headers,
    )

    assert response.status_code == 403, response.text
    assert db_session.get(SystemConfig, 1) is None
