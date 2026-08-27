"""Local legacy-object adoption contracts for the storage backend."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend, StorageCollisionError


@dataclass
class _FakeSettings:
    data_dir: Path
    thumb_dir: Path
    storage_identity: str = "test-installation"


def test_adopts_a_legacy_object_after_content_and_identity_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    content = b"legacy artifact"
    key = data_dir / "legacy.stl"
    key.write_bytes(content)

    receipt = backend.adopt_existing(
        str(key),
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )

    assert receipt.key == str(key)
    assert receipt.size == len(content)
    assert backend.creation_matches(receipt)


def test_rejects_legacy_adoption_when_the_content_digest_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    data_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(storage_backend, "settings", _FakeSettings(data_dir, thumb_dir))
    backend = LocalStorageBackend()
    key = data_dir / "legacy.stl"
    key.write_bytes(b"foreign")

    with pytest.raises(StorageCollisionError, match="content_mismatch"):
        backend.adopt_existing(
            str(key),
            expected_size=len(b"foreign"),
            expected_sha256=hashlib.sha256(b"expected").hexdigest(),
        )

    assert key.read_bytes() == b"foreign"
