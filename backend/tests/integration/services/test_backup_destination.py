"""Backup destination selection keeps manual and scheduled replicas independent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import backup_destination
from app.services.backup_destination import BackupTrigger


class TestConfiguredDestinations:
    def test_selects_only_manual_connections(
        self, make_storage_connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_storage_connection("Manual", automatic_backup_enabled=False)
        make_storage_connection("Automatic", manual_backup_enabled=False)
        monkeypatch.setattr(
            backup_destination,
            "destination_from_connection",
            lambda row: SimpleNamespace(name=row.name),
        )

        destinations = backup_destination.configured_destinations(BackupTrigger.MANUAL)

        assert [destination.name for destination in destinations] == ["Manual"]

    def test_selects_only_automatic_connections(
        self, make_storage_connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_storage_connection("Manual", automatic_backup_enabled=False)
        make_storage_connection("Automatic", manual_backup_enabled=False)
        monkeypatch.setattr(
            backup_destination,
            "destination_from_connection",
            lambda row: SimpleNamespace(name=row.name),
        )

        destinations = backup_destination.configured_destinations(
            BackupTrigger.AUTOMATIC
        )

        assert [destination.name for destination in destinations] == ["Automatic"]
