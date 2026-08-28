"""Shared local-storage setup for the storage backend unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend


@dataclass
class FakeSettings:
    data_dir: Path
    thumb_dir: Path
    backup_dir: Path | None = None
    storage_identity: str = "test-installation"


@pytest.fixture
def configured_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> LocalStorageBackend:
    """A local adapter rooted entirely inside the test's throwaway directory."""
    settings = FakeSettings(
        data_dir=tmp_path / "files",
        thumb_dir=tmp_path / "thumbs",
        backup_dir=tmp_path / "backups",
    )
    settings.data_dir.mkdir()
    settings.thumb_dir.mkdir()
    assert settings.backup_dir is not None
    settings.backup_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", settings)
    return LocalStorageBackend()
