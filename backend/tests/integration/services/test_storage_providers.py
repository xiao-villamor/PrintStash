"""Storage-provider configuration stays typed, discoverable, and safe to expose.

These integration tests exercise the provider registry and runtime configuration
against the real Pydantic models and SQLite-backed settings so a new provider or
credential path cannot silently drift from the public API.
"""

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


class TestStorageProviders:
    def test_catalogue_exposes_stable_non_empty_categories(self) -> None:
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
            "gdrive",
        }
        assert {provider.category for provider in providers} == set(ProviderCategory)
        assert all(provider.fields for provider in providers)

    @pytest.mark.parametrize(
        ("provider", "region", "account_id", "endpoint"),
        [
            (
                "cloudflare_r2",
                "ignored",
                "acct",
                "https://acct.r2.cloudflarestorage.com",
            ),
            (
                "backblaze_b2",
                "us-west-004",
                "",
                "https://s3.us-west-004.backblazeb2.com",
            ),
            ("wasabi", "eu-central-1", "", "https://s3.eu-central-1.wasabisys.com"),
        ],
    )
    def test_resolves_named_s3_provider_presets(
        self, provider: str, region: str, account_id: str, endpoint: str
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
        assert spec.options["region"] == (
            "auto" if provider == "cloudflare_r2" else region
        )

    def test_resolves_nextcloud_dav_path(self) -> None:
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

    def test_resolves_self_hosted_s3_with_path_style_addressing(self) -> None:
        spec = resolve_transport(
            S3ProviderConfig(
                provider="s3_self_hosted",
                bucket="models",
                endpoint_url="https://minio.example.test",
                region="us-east-1",
                access_key="access",
                secret_key="secret",
            )
        )

        assert spec.options["endpoint_url"] == "https://minio.example.test"
        assert spec.options["path_style"] is True

    def test_explicit_s3_addressing_style_overrides_the_provider_default(self) -> None:
        spec = resolve_transport(
            S3ProviderConfig(
                provider="s3_self_hosted",
                bucket="models",
                endpoint_url="https://minio.example.test",
                region="us-east-1",
                addressing_style="virtual",
                access_key="access",
                secret_key="secret",
            )
        )

        assert spec.options["addressing_style"] == "virtual"
        assert spec.options["path_style"] is False

    @pytest.mark.parametrize("root", ["", ".", "..", "safe/../escape"])
    def test_rejects_invalid_provider_roots(self, root: str) -> None:
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
    def test_rejects_invalid_sftp_auth(self, credentials: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            SFTPProviderConfig(
                provider="sftp",
                host="nas.example.test",
                username="printstash",
                **credentials,
            )

    @pytest.mark.parametrize(
        "credentials",
        [
            {"password": "secret"},
            {"private_key_path": "/run/keys/id_ed25519"},
            {
                "private_key_path": "/run/keys/id_ed25519",
                "passphrase": "key-secret",
            },
        ],
    )
    def test_accepts_exactly_one_sftp_authentication_mode(
        self,
        credentials: dict[str, str],
    ) -> None:
        config = SFTPProviderConfig(
            provider="sftp",
            host="nas.example.test",
            username="printstash",
            host_key="ssh-ed25519 AAAA",
            **credentials,
        )

        spec = resolve_transport(config)

        assert spec.options.get("password") == credentials.get("password")
        assert spec.options.get("private_key_path") == credentials.get(
            "private_key_path"
        )
        assert spec.options.get("passphrase") == credentials.get("passphrase")

    def test_rejects_sftp_activation_without_a_host_key(self) -> None:
        config = SFTPProviderConfig(
            provider="sftp",
            host="nas.example.test",
            username="printstash",
            password="secret",
        )

        with pytest.raises(ValueError, match="sftp_host_key_required"):
            resolve_transport(config)

    def test_public_provider_catalogue_needs_no_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/storage/providers")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {
            provider.id for provider in provider_catalogue()
        }

    def test_remote_providers_remain_visible_when_optional_binding_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.services.storage_operations.find_spec", lambda _name: None
        )

        providers = {
            provider.id: provider
            for provider in provider_catalogue()
            if provider.id in {"nextcloud", "webdav", "sftp"}
        }

        assert set(providers) == {"nextcloud", "webdav", "sftp"}
        assert all(not provider.available for provider in providers.values())
        assert all(not provider.selectable for provider in providers.values())
        assert all(
            provider.disabled_reason == "storage_dependency_missing"
            for provider in providers.values()
        )
        s3 = next(provider for provider in provider_catalogue() if provider.id == "s3")
        assert s3.selectable and s3.uses["vault"].available
        assert not s3.uses["library"].available
        assert not s3.uses["backup"].available
        assert not s3.uses["vault"].endpoint_proven

    def test_provider_secrets_are_encrypted(
        self,
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
        provider, sanitized = runtime_config.get_sanitized_storage_provider(
            db_session
        ) or (
            "",
            {},
        )
        assert provider == "webdav"
        assert sanitized["secret_fields_set"] == ["password"]
        assert "password" not in sanitized

    def test_same_provider_omission_preserves_secret(
        self,
        db_session: Session,
    ) -> None:
        row = runtime_config.update_storage_provider(
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

        preserved = runtime_config.resolve_requested_storage_provider(
            row,
            provider="webdav",
            raw_config={
                "provider": "webdav",
                "endpoint_url": "https://dav.example.test",
                "username": "user",
                "root": "models",
            },
        )
        assert isinstance(preserved, WebDAVProviderConfig)
        assert preserved.password == "top-secret"

    def test_provider_change_clears_old_secret(
        self,
        db_session: Session,
        tmp_path,
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

        runtime_config.update_storage_provider(
            db_session,
            provider="local",
            raw_config={
                "provider": "local",
                "data_dir": str(tmp_path / "files"),
                "thumb_dir": str(tmp_path / "thumbs"),
                "root": "models",
            },
        )
        _, sanitized = runtime_config.get_sanitized_storage_provider(db_session) or (
            "",
            {},
        )
        assert sanitized["secret_fields_set"] == []

    def test_sftp_authentication_mode_change_clears_old_secrets(
        self,
        db_session: Session,
    ) -> None:
        row = runtime_config.update_storage_provider(
            db_session,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "printstash",
                "host_key": "ssh-ed25519 AAAA",
                "password": "old-password",
                "root": "models",
            },
        )

        changed = runtime_config.resolve_requested_storage_provider(
            row,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "printstash",
                "host_key": "ssh-ed25519 AAAA",
                "private_key_path": "/run/keys/id_ed25519",
                "passphrase": "new-passphrase",
                "root": "models",
            },
        )

        assert isinstance(changed, SFTPProviderConfig)
        assert changed.password == ""
        assert changed.private_key_path == "/run/keys/id_ed25519"
        assert changed.passphrase == "new-passphrase"


class TestSharedFieldMetadata:
    def test_catalogue_fields_keep_typed_secret_requirements(self):
        from app.services.storage_providers import (
            provider_catalogue,
            provider_secret_fields,
        )

        for provider in provider_catalogue():
            for use, fields in provider.fields_by_use.items():
                names = {field.name for field in fields if field.secret}
                assert names == provider_secret_fields(provider.id), (provider.id, use)
                assert all(
                    field.input_type == "password" for field in fields if field.secret
                )

    def test_remote_uses_share_preset_configuration_fields(self):
        from app.services.storage_providers import provider_catalogue

        for provider in provider_catalogue():
            assert provider.fields_by_use["library"] == provider.fields_by_use["backup"]
            assert {field.name for field in provider.fields} == {
                field.name for field in provider.fields_by_use["library"]
            }

    def test_sftp_declares_alternative_authentication(self):
        from app.services.storage_providers import provider_catalogue

        provider = next(row for row in provider_catalogue() if row.id == "sftp")
        assert {
            "kind": "exactly_one",
            "fields": ["password", "private_key_path"],
            "message": "Use either a password or a private key path.",
        } in provider.requirements

    @pytest.mark.parametrize(
        "provider,values",
        [
            ("cloudflare_r2", {"account_id": "account", "bucket": "models"}),
            ("backblaze_b2", {"region": "us-west-004", "bucket": "models"}),
            ("wasabi", {"region": "us-east-1", "bucket": "models"}),
            (
                "s3_self_hosted",
                {"endpoint_url": "https://s3.example.test", "bucket": "models"},
            ),
            (
                "nextcloud",
                {"endpoint_url": "https://cloud.example.test", "username": "owner"},
            ),
        ],
    )
    def test_connection_presets_resolve_like_vault_presets(self, provider, values):
        from app.db.models import LibrarySourceKind
        from app.services.storage_connections import parse_connection_config
        from app.services.storage_providers import (
            parse_provider_config,
            resolve_transport,
        )

        remote_kind = (
            LibrarySourceKind.WEBDAV
            if provider == "nextcloud"
            else LibrarySourceKind.S3
        )
        secrets = (
            {"password": "password"}
            if provider == "nextcloud"
            else {"access_key": "access", "secret_key": "secret"}
        )
        public = {"provider": provider, "root": "same-prefix", **values}
        vault = resolve_transport(parse_provider_config({**public, **secrets}))
        connection = resolve_transport(
            parse_connection_config(remote_kind, public, secrets)
        )
        assert connection == vault


class TestProviderRegionDefaults:
    def test_ordinary_s3_forms_offer_an_explicit_signing_region(self):
        from app.services.storage_providers import provider_fields

        for use in ("vault", "library", "backup"):
            fields = {field.name: field for field in provider_fields("s3", use=use)}
            assert fields["region"].default == "us-east-1"
