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
from app.modules.administration import runtime_config
from app.modules.storage.storage_providers import (
    PRESETS,
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
            *PRESETS,
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
            "app.modules.storage.storage_operations.find_spec", lambda _name: None
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

    @pytest.mark.parametrize("provider", ["sftp", "hetzner_storage_box"])
    def test_sftp_authentication_mode_change_clears_old_secrets(
        self,
        db_session: Session,
        provider: str,
    ) -> None:
        row = runtime_config.update_storage_provider(
            db_session,
            provider=provider,
            raw_config={
                "provider": provider,
                "host": "nas.example.test",
                "username": "printstash",
                "host_key": "ssh-ed25519 AAAA",
                "password": "old-password",
                "root": "models",
            },
        )

        changed = runtime_config.resolve_requested_storage_provider(
            row,
            provider=provider,
            raw_config={
                "provider": provider,
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
        from app.modules.storage.storage_providers import (
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

    def test_local_folders_use_the_names_setup_shows(self):
        # Setup's check result and review call these folders "Your files" and
        # "Previews"; the form must use the same names so they read as one thing.
        from app.modules.storage.storage_providers import provider_catalogue

        local = next(p for p in provider_catalogue() if p.id == "local")
        labels = {field.name: field.label for field in local.fields_by_use["vault"]}
        assert labels["data_dir"] == "Your files"
        assert labels["thumb_dir"] == "Previews"

    def test_local_files_folder_explains_container_paths(self):
        from app.modules.storage.storage_providers import provider_catalogue

        local = next(p for p in provider_catalogue() if p.id == "local")
        data_dir = next(f for f in local.fields_by_use["vault"] if f.name == "data_dir")
        assert "inside the container" in data_dir.help

    def test_remote_uses_share_preset_configuration_fields(self):
        from app.modules.storage.storage_providers import provider_catalogue

        for provider in provider_catalogue():
            assert provider.fields_by_use["library"] == provider.fields_by_use["backup"]
            assert {field.name for field in provider.fields} == {
                field.name for field in provider.fields_by_use["library"]
            }

    def test_sftp_declares_alternative_authentication(self):
        from app.modules.storage.storage_providers import provider_catalogue

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
        from app.modules.storage.storage_connections import parse_connection_config
        from app.modules.storage.storage_providers import (
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
        from app.modules.storage.storage_providers import provider_fields

        for use in ("vault", "library", "backup"):
            fields = {field.name: field for field in provider_fields("s3", use=use)}
            assert fields["region"].default == "us-east-1"


class TestPresetConfiguration:
    @pytest.mark.parametrize("provider", list(PRESETS))
    def test_resolves_each_preset_to_its_declared_transport(self, provider):
        from app.modules.storage.storage_providers import PRESETS, parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        spec = resolve_transport(parse_provider_config(preset_configuration(provider)))

        assert spec.kind.value == PRESETS[provider]["transport"]
        assert spec.provider == provider

    @pytest.mark.parametrize(
        ("provider", "field", "expected"),
        [
            ("minio", "region", "us-east-1"),
            ("garage", "region", "garage"),
            ("seaweedfs", "region", "us-east-1"),
            ("hetzner_object_storage", "region", "fsn1"),
            ("hetzner_storage_box", "port", 23),
            ("koofr", "endpoint_url", "https://app.koofr.net/dav/Koofr"),
        ],
    )
    def test_applies_researched_defaults_to_api_forms(self, provider, field, expected):
        from app.modules.storage.storage_providers import (
            parse_provider_config,
            provider_fields,
        )
        from tests.fixtures.storage_presets import preset_configuration

        raw = preset_configuration(provider)
        raw.pop(field, None)

        config = parse_provider_config(raw)

        assert getattr(config, field) == expected
        assert (
            next(
                entry.default
                for entry in provider_fields(provider)
                if entry.name == field
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("provider", "field", "override"),
        [
            ("garage", "region", "custom-region"),
            ("hetzner_storage_box", "port", 22),
            ("koofr", "endpoint_url", "https://dav.example.test/custom"),
        ],
    )
    def test_preserves_explicit_preset_overrides(self, provider, field, override):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        spec = resolve_transport(
            parse_provider_config({**preset_configuration(provider), field: override})
        )

        assert spec.options[field] == override

    def test_derives_hetzner_object_storage_endpoint_from_location(self):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        raw = preset_configuration("hetzner_object_storage")
        raw.pop("endpoint_url")

        spec = resolve_transport(parse_provider_config({**raw, "region": "hel1"}))

        assert spec.options["endpoint_url"] == "https://hel1.your-objectstorage.com"
        assert spec.options["path_style"] is True

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://user:secret@dav.example.test",
            "https://dav.example.test?token=secret",
            "ftp://dav.example.test",
            "https://dav.example.test/#secret",
        ],
    )
    @pytest.mark.parametrize("provider", ["minio", "koofr"])
    def test_rejects_unsafe_preset_endpoints(self, provider, endpoint):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        config = parse_provider_config(
            {**preset_configuration(provider), "endpoint_url": endpoint}
        )

        with pytest.raises(ValueError, match="provider_endpoint_invalid"):
            resolve_transport(config)

    def test_requires_the_storage_box_host_key(self):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        config = parse_provider_config(
            {**preset_configuration("hetzner_storage_box"), "host_key": ""}
        )

        with pytest.raises(ValueError, match="sftp_host_key_required"):
            resolve_transport(config)

    def test_delivery_is_independent_of_deletion_safety(self):
        provider = next(entry for entry in provider_catalogue() if entry.id == "garage")

        assert provider.expected_tier == "guarded"
        assert provider.delivery.model_dump() == {
            "signed_get": True,
            "requires_endpoint_proof": True,
            "requires_cors_proof": True,
            "same_origin_fallback": True,
        }
        assert provider.uses["vault"].endpoint_proven is False

    def test_preserves_google_drive_roles(self):
        provider = next(entry for entry in provider_catalogue() if entry.id == "gdrive")

        assert provider.uses["library"].supported is True
        assert provider.uses["backup"].supported is True
        assert provider.uses["vault"].supported is False
        assert provider.delivery.signed_get is False

    def test_missing_sftp_dependency_disables_storage_box(self, monkeypatch):
        from app.modules.storage import storage_operations

        monkeypatch.setattr(storage_operations, "find_spec", lambda name: None)

        provider = next(
            entry for entry in provider_catalogue() if entry.id == "hetzner_storage_box"
        )

        assert provider.selectable is False
        assert provider.uses["backup"].reason == "storage_dependency_missing"

    @pytest.mark.parametrize("provider", ["minio", "garage", "seaweedfs"])
    def test_requires_self_hosted_preset_endpoints(self, provider):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        raw = preset_configuration(provider)
        raw.pop("endpoint_url")

        with pytest.raises(ValueError, match="s3_endpoint_required"):
            resolve_transport(parse_provider_config(raw))

    @pytest.mark.parametrize(
        ("provider", "transport", "field", "value"),
        [
            ("truenas", "local", "data_dir", "/tmp/models"),
            ("garage", "s3", "s3_bucket", "models"),
            ("koofr", "webdav", "webdav_username", "owner"),
            ("hetzner_storage_box", "sftp", "sftp_host", "box.example.test"),
        ],
    )
    def test_loads_scalar_environment_presets(
        self, monkeypatch, provider, transport, field, value
    ):
        from types import SimpleNamespace

        values = dict(
            storage_provider=provider,
            storage_root="root",
            data_dir="",
            thumb_dir="",
            s3_bucket="",
            s3_region="",
            s3_endpoint_url="",
            s3_addressing_style="",
            s3_access_key="",
            s3_secret_key="",
            webdav_endpoint_url="",
            webdav_username="",
            webdav_password="",
            sftp_host="",
            sftp_port="",
            sftp_username="",
            sftp_host_key="",
            sftp_password="",
            sftp_private_key_path="",
            sftp_passphrase="",
        )
        values[field] = value
        monkeypatch.setattr(runtime_config, "settings", SimpleNamespace(**values))

        result = runtime_config._typed_environment_provider_config()

        assert result["provider"] == provider
        assert result[field.removeprefix(transport + "_")] == value

    @pytest.mark.parametrize("region", ["auto", "../escape", "https://region"])
    def test_rejects_invalid_hetzner_locations(self, region):
        from app.modules.storage.storage_providers import parse_provider_config
        from tests.fixtures.storage_presets import preset_configuration

        config = parse_provider_config(
            {**preset_configuration("hetzner_object_storage"), "region": region}
        )

        with pytest.raises(ValueError, match="s3_region_required"):
            resolve_transport(config)

    @pytest.mark.parametrize("provider", ["smb", "azure", "gcs", "dropbox"])
    def test_keeps_deferred_transports_unavailable(self, provider):
        from app.modules.storage.storage_providers import provider_transport

        with pytest.raises(ValueError, match="storage_provider_unknown"):
            provider_transport(provider)
