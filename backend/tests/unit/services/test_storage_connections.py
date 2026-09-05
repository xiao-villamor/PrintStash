"""Purpose-agnostic storage profiles keep provider secrets behind one boundary."""

import pytest

from app.db.models import LibrarySourceKind, StorageConnection
from app.services.storage_connections import (
    StorageConnectionConfigError,
    load_connection_config,
    serialize_connection_config,
)


class TestSerializeConnectionConfig:
    def test_google_drive_secrets_are_split_from_returnable_configuration(
        self,
    ) -> None:
        configuration, secrets = serialize_connection_config(
            LibrarySourceKind.GDRIVE,
            {"client_id": "client", "root": "/PrintStash/backups/"},
            {"client_secret": "secret", "refresh_token": "refresh"},
        )

        assert configuration == {
            "provider": "gdrive",
            "root": "PrintStash/backups",
            "client_id": "client",
        }
        assert secrets == {"client_secret": "secret", "refresh_token": "refresh"}

    def test_rejects_unknown_provider_fields(self) -> None:
        with pytest.raises(StorageConnectionConfigError):
            serialize_connection_config(
                LibrarySourceKind.GDRIVE,
                {
                    "client_id": "client",
                    "root": "PrintStash",
                    "access_token": "leak",
                },
                {"client_secret": "secret", "refresh_token": "refresh"},
            )


class TestStoredConfiguration:
    @pytest.mark.parametrize("configuration", ["not-json-secret", "[]", "null"])
    def test_corrupt_profile_returns_a_stable_error(self, configuration):
        profile = StorageConnection(
            name="Corrupt profile",
            kind=LibrarySourceKind.S3,
            config_json=configuration,
            secret_json="{}",
        )
        with pytest.raises(StorageConnectionConfigError) as error:
            load_connection_config(profile)
        assert str(error.value) == "storage_connection_invalid"
        assert "not-json-secret" not in str(error.value)

    def test_self_hosted_preset_requires_an_endpoint(self):
        with pytest.raises(
            StorageConnectionConfigError, match="storage_connection_invalid"
        ):
            serialize_connection_config(
                LibrarySourceKind.S3,
                {"provider": "s3_self_hosted", "bucket": "bucket"},
                {"access_key": "access", "secret_key": "secret"},
            )
