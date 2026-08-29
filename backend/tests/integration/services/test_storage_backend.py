"""Configured storage adapters report measured guarantees through one seam.

The local adapter must not infer POSIX guarantees merely because it received a
filesystem path; setup probes both managed roots and exposes the weakest result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import (
    LocalStorageBackend,
    ObjectIdentity,
    S3StorageBackend,
    StorageCollisionError,
    StorageConfigurationError,
    StorageTier,
    UnavailableStorageBackend,
    enroll_legacy_local_root,
)


@dataclass
class _LocalSettings:
    data_dir: Path
    thumb_dir: Path
    backup_dir: Path
    storage_identity: str = "a" * 64


@dataclass
class _S3Settings:
    s3_bucket: str = "vault"
    s3_region: str = "us-east-1"
    s3_access_key: str = "fake-access"
    s3_secret_key: str = "fake-secret"
    s3_endpoint_url: str = "https://s3.invalid"
    storage_identity: str = "a" * 64
    s3_lifecycle_expiration_days: int = 0
    s3_lifecycle_transition_days: int = 0


def _enroll_local_roots(configured: _LocalSettings) -> None:
    configured.data_dir.mkdir(parents=True)
    configured.thumb_dir.mkdir(parents=True)
    for role, root in (("data", configured.data_dir), ("thumb", configured.thumb_dir)):
        (root / ".printstash-storage-root.json").write_text(
            '{"format":1,"installation":"%s","role":"%s"}'
            % (configured.storage_identity, role),
            encoding="utf-8",
        )


class _S3Client:
    def __init__(self, versioning_status: str | None) -> None:
        self.versioning_status = versioning_status

    def head_bucket(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def get_bucket_versioning(self, **_kwargs: object) -> dict[str, str]:
        if self.versioning_status is None:
            return {}
        return {"Status": self.versioning_status}


@pytest.fixture
def local_restore_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[LocalStorageBackend, dict[str, Path]]:
    configured = _LocalSettings(
        tmp_path / "files",
        tmp_path / "thumbs",
        tmp_path / "backups",
    )
    external_root = tmp_path / "nas"
    monkeypatch.setattr(storage_backend, "settings", configured)
    return LocalStorageBackend(external_roots=(external_root,)), {
        "vault": configured.data_dir,
        "thumbnail": configured.thumb_dir,
        "backup": configured.backup_dir,
        "external": external_root,
    }


class TestStorageBackendValidateRestoreKey:
    def test_unavailable_provider_is_fail_closed(self) -> None:
        backend = UnavailableStorageBackend("invalid_provider")

        assert backend.health_probe()["ok"] is False
        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            backend.create_bytes(b"must-not-write", "any-key")

    def test_rejects_a_key_outside_the_backend_namespace(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(storage_backend, "settings", _S3Settings())
        monkeypatch.setattr("boto3.client", lambda **_kwargs: _S3Client(None))
        backend = S3StorageBackend()

        with pytest.raises(
            StorageCollisionError,
            match="storage_key_outside_managed_root",
        ):
            backend.validate_restore_key("outside/restore.gcode")


class TestLocalStorageBackendValidateRestoreKey:
    @pytest.mark.parametrize(
        "root_role",
        [
            pytest.param("vault", id="vault"),
            pytest.param("thumbnail", id="thumbnail"),
            pytest.param("backup", id="backup"),
            pytest.param("external", id="external"),
        ],
    )
    def test_accepts_a_descendant_of_a_managed_root(
        self,
        local_restore_backend: tuple[LocalStorageBackend, dict[str, Path]],
        root_role: str,
    ) -> None:
        backend, roots = local_restore_backend

        result = backend.validate_restore_key(str(roots[root_role] / "restore.gcode"))

        assert result is None

    @pytest.mark.parametrize(
        "root_role",
        [
            pytest.param("vault", id="vault"),
            pytest.param("thumbnail", id="thumbnail"),
            pytest.param("backup", id="backup"),
            pytest.param("external", id="external"),
        ],
    )
    def test_rejects_a_managed_root_itself(
        self,
        local_restore_backend: tuple[LocalStorageBackend, dict[str, Path]],
        root_role: str,
    ) -> None:
        backend, roots = local_restore_backend

        with pytest.raises(
            StorageConfigurationError,
            match="backup_restore_key_outside_storage",
        ):
            backend.validate_restore_key(str(roots[root_role]))

    def test_rejects_a_destination_outside_managed_roots(
        self,
        tmp_path: Path,
        local_restore_backend: tuple[LocalStorageBackend, dict[str, Path]],
    ) -> None:
        backend, _roots = local_restore_backend

        with pytest.raises(
            StorageConfigurationError,
            match="backup_restore_key_outside_storage",
        ):
            backend.validate_restore_key(str(tmp_path / "outside" / "restore.gcode"))

    def test_rejects_a_key_without_a_direct_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        local_restore_backend: tuple[LocalStorageBackend, dict[str, Path]],
    ) -> None:
        backend, _roots = local_restore_backend
        monkeypatch.setattr(backend, "direct_path", lambda _key: None)

        with pytest.raises(
            StorageConfigurationError,
            match="local_restore_key_not_a_path",
        ):
            backend.validate_restore_key("opaque:restore-key")


class TestLocalStorageBackendEnsureSetup:
    def test_markerless_legacy_root_requires_evidence_or_explicit_enrollment(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "legacy-files"
        root.mkdir()
        (root / "old.stl").write_bytes(b"legacy")

        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="test-installation",
            proofs=[],
        )
        assert not (root / ".printstash-storage-root.json").exists()

        assert enroll_legacy_local_root(
            root,
            role="data",
            installation="test-installation",
            proofs=[],
            allow_empty=True,
        )

    def test_does_not_create_a_missing_configured_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "missing-files",
            tmp_path / "missing-thumbs",
            tmp_path / "backups",
        )
        monkeypatch.setattr(storage_backend, "settings", configured)
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert not configured.data_dir.exists()
        assert not configured.thumb_dir.exists()
        assert backend.capabilities.tier is StorageTier.UNGUARDED
        assert backend.health_probe()["ok"] is False

    def test_hardlinkless_root_uses_exclusive_create_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(storage_backend, "detect_fs_kind", lambda _path: "network")
        monkeypatch.setattr(
            os,
            "link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(18, "cross-device")
            ),
        )
        backend = LocalStorageBackend()
        backend.ensure_setup()
        key = str(configured.data_dir / "guarded.bin")

        receipt = backend.create_bytes(b"guarded", key)

        assert backend.capabilities.tier is StorageTier.GUARDED
        assert receipt.device is None
        assert Path(key).read_bytes() == b"guarded"

    def test_reports_verified_for_local_roots_with_hardlinks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(storage_backend, "detect_fs_kind", lambda _path: "local")
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is StorageTier.VERIFIED

    def test_reports_unguarded_when_a_root_rejects_hardlinks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(storage_backend, "detect_fs_kind", lambda _path: "local")
        monkeypatch.setattr(
            os,
            "link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unsupported")),
        )
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is StorageTier.GUARDED

    def test_reports_guarded_for_a_network_filesystem(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(storage_backend, "detect_fs_kind", lambda _path: "network")
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is StorageTier.GUARDED

    def test_uses_the_weaker_result_across_managed_roots(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(
            storage_backend,
            "detect_fs_kind",
            lambda path: "network" if Path(path) == configured.thumb_dir else "local",
        )
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is StorageTier.GUARDED

    def test_reports_directory_fsync_as_a_diagnostic(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = _LocalSettings(
            tmp_path / "files", tmp_path / "thumbs", tmp_path / "backups"
        )
        _enroll_local_roots(configured)
        monkeypatch.setattr(storage_backend, "settings", configured)
        monkeypatch.setattr(storage_backend, "detect_fs_kind", lambda _path: "local")
        monkeypatch.setattr(
            storage_backend,
            "_fsync_directory",
            lambda _path: (_ for _ in ()).throw(OSError("unsupported")),
        )
        backend = LocalStorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is StorageTier.GUARDED
        assert backend.health_probe()["diagnostics"]["directory_fsync"] is False


class TestS3StorageBackendEnsureSetup:
    @pytest.mark.parametrize(
        ("versioning_status", "tier", "identity"),
        [
            pytest.param(
                "Enabled",
                StorageTier.VERIFIED,
                ObjectIdentity.VERSION,
                id="enabled",
            ),
            pytest.param(
                None,
                StorageTier.GUARDED,
                ObjectIdentity.ETAG,
                id="absent",
            ),
            pytest.param(
                "Suspended",
                StorageTier.GUARDED,
                ObjectIdentity.ETAG,
                id="suspended",
            ),
        ],
    )
    def test_derives_the_tier_from_bucket_versioning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        versioning_status: str | None,
        tier: StorageTier,
        identity: ObjectIdentity,
    ) -> None:
        client = _S3Client(versioning_status)
        monkeypatch.setattr(storage_backend, "settings", _S3Settings())
        monkeypatch.setattr("boto3.client", lambda **_kwargs: client)
        backend = S3StorageBackend()

        backend.ensure_setup()

        assert backend.capabilities.tier is tier
        assert backend.capabilities.object_identity is identity
