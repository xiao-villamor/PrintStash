from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session

from app.db.models import SystemConfig
from app.services import runtime_config
from app.services.storage_providers import (
    ProviderCategory,
    S3ProviderConfig,
    SFTPProviderConfig,
    WebDAVProviderConfig,
    provider_catalogue,
    resolve_transport,
)


def test_catalogue_has_four_non_empty_categories_and_stable_ids() -> None:
    providers = provider_catalogue()
    assert {provider.id for provider in providers} == {
        "local",
        "s3",
        "cloudflare_r2",
        "backblaze_b2",
        "wasabi",
        "s3_self_hosted",
        "nextcloud",
        "webdav",
        "sftp",
    }
    assert {provider.category for provider in providers} == set(ProviderCategory)
    assert all(provider.fields for provider in providers)


@pytest.mark.parametrize(
    ("provider", "region", "account_id", "endpoint"),
    [
        ("cloudflare_r2", "ignored", "acct", "https://acct.r2.cloudflarestorage.com"),
        ("backblaze_b2", "us-west-004", "", "https://s3.us-west-004.backblazeb2.com"),
        ("wasabi", "eu-central-1", "", "https://s3.eu-central-1.wasabisys.com"),
    ],
)
def test_resolves_named_s3_provider_presets(
    provider: str, region: str, account_id: str, endpoint: str
) -> None:
    config = S3ProviderConfig(
        provider=provider,  # type: ignore[arg-type]
        bucket="models",
        region=region,
        account_id=account_id,
        access_key="access",
        secret_key="secret",
    )
    spec = resolve_transport(config)
    assert spec.options["endpoint_url"] == endpoint
    assert spec.options["region"] == ("auto" if provider == "cloudflare_r2" else region)


def test_resolves_nextcloud_dav_path() -> None:
    spec = resolve_transport(
        WebDAVProviderConfig(
            provider="nextcloud",
            endpoint_url="https://cloud.example.test/nextcloud",
            username="a user",
            password="secret",
            root="Print Stash",
        )
    )
    assert spec.options["endpoint_url"] == (
        "https://cloud.example.test/nextcloud/remote.php/dav/files/a%20user"
    )
    assert spec.namespace == "webdav/Print Stash"


@pytest.mark.parametrize("root", ["", ".", "..", "safe/../escape"])
def test_rejects_invalid_provider_roots(root: str) -> None:
    with pytest.raises(ValidationError, match="storage_root"):
        WebDAVProviderConfig(
            provider="webdav",
            endpoint_url="https://dav.example.test",
            username="user",
            password="secret",
            root=root,
        )


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"password": "secret", "private_key_path": "/run/keys/id_ed25519"},
        {"passphrase": "secret"},
        {"private_key_path": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_rejects_invalid_sftp_auth(credentials: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        SFTPProviderConfig(
            provider="sftp",
            host="nas.example.test",
            username="printstash",
            **credentials,
        )


def test_public_provider_catalogue_needs_no_auth(client: TestClient) -> None:
    response = client.get("/api/v1/storage/providers")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {
        provider.id for provider in provider_catalogue()
    }


def test_provider_secrets_are_encrypted_and_sanitized(
    db_session: Session,
) -> None:
    runtime_config.update_storage_provider(
        db_session,
        provider="webdav",
        raw_config={
            "provider": "webdav",
            "endpoint_url": "https://dav.example.test",
            "username": "user",
            "password": "top-secret",
            "root": "models",
        },
    )
    row = db_session.get(SystemConfig, 1)
    assert row is not None
    assert "top-secret" not in (row.storage_provider_config_json or "")
    provider, sanitized = runtime_config.get_sanitized_storage_provider(db_session) or (
        "",
        {},
    )
    assert provider == "webdav"
    assert sanitized["secret_fields_set"] == ["password"]
    assert "password" not in sanitized
