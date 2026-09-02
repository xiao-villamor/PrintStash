"""Purpose-agnostic storage profiles keep provider secrets behind one boundary."""

import pytest

from app.db.models import LibrarySourceKind
from app.services.storage_connections import (
    StorageConnectionConfigError,
    serialize_connection_config,
)


def test_google_drive_secrets_are_split_from_returnable_configuration() -> None:
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


def test_profile_validation_rejects_unknown_provider_fields() -> None:
    with pytest.raises(StorageConnectionConfigError):
        serialize_connection_config(
            LibrarySourceKind.GDRIVE,
            {"client_id": "client", "root": "PrintStash", "access_token": "leak"},
            {"client_secret": "secret", "refresh_token": "refresh"},
        )
