"""Local storage namespaces and ownership evidence stay fail-closed.

Reclaim and legacy adoption may remove bytes only after the path, size, and
content belong to a configured managed root.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    ObjectIdentity,
    StorageCapabilities,
    StorageCollisionError,
    StorageConfigurationError,
    enroll_legacy_local_root,
)
from tests._env import use_local_storage


def _configure_local_storage(tmp_path: Path) -> LocalStorageBackend:
    """Use the same enrolled local-root setup as the application tests."""
    use_local_storage(tmp_path)
    from app.core.config import _overlay

    backup = tmp_path / "backups"
    backup.mkdir()
    _overlay["backup_dir"] = backup
    return LocalStorageBackend()


class TestLocalNamespaces:
    def test_direct_adapter_identity_is_256_bit_when_unconfigured(
        self,
        configured_backend: LocalStorageBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(storage_backend.settings, "storage_identity", "")

        assert len(configured_backend._installation_identity()) == 64
        monkeypatch.setattr(storage_backend.settings, "storage_identity", "malformed")
        assert len(configured_backend._installation_identity()) == 64

    @pytest.mark.parametrize(
        "key_kind",
        [
            pytest.param("data", id="data"),
            pytest.param("thumb", id="thumb"),
            pytest.param("backup", id="backup"),
        ],
    )
    def test_reports_the_owned_namespace_for_managed_key(
        self, configured_backend: LocalStorageBackend, key_kind: str
    ) -> None:
        root = getattr(storage_backend.settings, f"{key_kind}_dir")
        key = str(root / "nested" / "object.bin")

        assert configured_backend.namespace_for(key) == f"{key_kind}:{root}"

    def test_rejects_a_key_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.namespace_for(str(tmp_path / "not-owned.bin"))


class TestLocalKeyDerivation:
    @pytest.mark.parametrize(
        ("derive", "root", "expected"),
        [
            pytest.param(
                lambda backend: backend.blob_key("bracket", 2, "part.stl"),
                "data",
                "bracket/v2/part.stl",
                id="artifact",
            ),
            pytest.param(
                lambda backend: backend.thumbnail_key(7),
                "thumb",
                "7.webp",
                id="thumbnail",
            ),
            pytest.param(
                lambda backend: backend.legacy_thumbnail_key(7),
                "thumb",
                "7.png",
                id="legacy-thumbnail",
            ),
            pytest.param(
                lambda backend: backend.source_cover_key(9),
                "thumb",
                "source-covers/9.webp",
                id="source-cover",
            ),
            pytest.param(
                lambda backend: backend.capture_upload_slot_key("slot-1"),
                "data",
                "capture-slots/slot-1",
                id="capture-slot",
            ),
            pytest.param(
                lambda backend: backend.stl_cache_key("a" * 64),
                "thumb",
                f"stl-cache/{'a' * 64}.stl",
                id="stl-cache",
            ),
            pytest.param(
                lambda backend: backend.collection_image_key(3, "hero.webp"),
                "thumb",
                "collection-images/3/hero.webp",
                id="collection-image",
            ),
            pytest.param(
                lambda backend: backend.document_file_key(4, "manual.pdf"),
                "data",
                "documents/4/manual.pdf",
                id="document-file",
            ),
            pytest.param(
                lambda backend: backend.document_image_key(4, "figure.webp"),
                "thumb",
                "document-images/4/figure.webp",
                id="document-image",
            ),
            pytest.param(
                lambda backend: backend.multipart_model_cover_key(5, "cover.webp"),
                "thumb",
                "multipart-covers/5/cover.webp",
                id="multipart-model-cover",
            ),
        ],
    )
    def test_puts_each_object_kind_under_the_expected_root(
        self,
        configured_backend: LocalStorageBackend,
        derive,
        root: str,
        expected: str,
    ) -> None:
        assert derive(configured_backend) == str(
            getattr(storage_backend.settings, f"{root}_dir") / expected
        )


class TestLocalReclaim:
    def test_reclaim_pins_the_original_inode_before_a_path_replacement(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        destination = tmp_path / "files" / "reclaim-race.bin"
        destination.write_bytes(b"owned")
        real_rename = storage_backend.os.rename

        def replace_after_pin(source, target, **kwargs):
            replacement = destination.with_name("replacement.bin")
            replacement.write_bytes(b"other")
            storage_backend.os.replace(replacement, destination)
            return real_rename(source, target, **kwargs)

        monkeypatch.setattr(storage_backend.os, "rename", replace_after_pin)

        assert (
            configured_backend.reclaim_unverified(
                str(destination), expected_size=5, expected_etag=None
            )
            is False
        )
        assert destination.read_bytes() == b"other"

    def test_treats_a_missing_object_as_reclaimed(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = str(tmp_path / "files" / "missing.bin")

        assert (
            configured_backend.reclaim_unverified(
                key, expected_size=10, expected_etag=None
            )
            is True
        )

    def test_reclaims_an_object_matching_size_with_digest_proof(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        payload = b"owned payload"
        key = tmp_path / "files" / "reclaim.bin"
        key.write_bytes(payload)

        assert (
            configured_backend.reclaim_unverified(
                str(key),
                expected_size=len(payload),
                expected_etag=configured_backend.object_info(str(key)).etag,  # type: ignore[union-attr]
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            is True
        )
        assert not key.exists()

    def test_preserves_an_object_with_a_size_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-size.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=999, expected_etag=None
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_preserves_an_object_with_a_digest_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-digest.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=7, expected_etag=None, expected_sha256="0" * 64
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_preserves_an_object_with_an_etag_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-etag.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=7, expected_etag='"different-etag"'
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_rejects_reclaim_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.reclaim_unverified(
                str(tmp_path / "external.bin"), expected_size=0, expected_etag=None
            )


class TestLocalAdoption:
    def test_adopts_a_matching_legacy_object(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        payload = b"legacy artifact"
        key = tmp_path / "files" / "legacy.stl"
        key.write_bytes(payload)

        receipt = configured_backend.adopt_existing(
            str(key),
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )

        assert receipt.token == hashlib.sha256(payload).hexdigest()
        assert configured_backend.creation_matches(receipt)

    def test_rejects_a_legacy_object_with_wrong_digest(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "legacy.stl"
        key.write_bytes(b"legacy artifact")

        with pytest.raises(StorageCollisionError, match="content_mismatch"):
            configured_backend.adopt_existing(
                str(key), expected_size=15, expected_sha256="0" * 64
            )

    def test_rejects_a_legacy_directory(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "legacy-directory"
        key.mkdir()

        with pytest.raises(StorageCollisionError, match="content_mismatch"):
            configured_backend.adopt_existing(
                str(key), expected_size=0, expected_sha256="0" * 64
            )

    def test_rejects_a_legacy_object_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "external.stl"
        key.write_bytes(b"legacy artifact")

        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.adopt_existing(
                str(key), expected_size=15, expected_sha256="0" * 64
            )

    def test_rejects_a_missing_legacy_object(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="unavailable"):
            configured_backend.adopt_existing(
                str(tmp_path / "files" / "missing.stl"),
                expected_size=1,
                expected_sha256="0" * 64,
            )


class TestLocalRootSafetyBranches:
    @pytest.mark.parametrize(
        "root_name",
        [
            pytest.param("data", id="data-root"),
            pytest.param("thumb", id="thumb-root"),
            pytest.param("backup", id="backup-root"),
        ],
    )
    def test_accepts_a_descendant_of_a_configured_root(
        self, tmp_path: Path, root_name: str
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        root = getattr(storage_backend.settings, f"{root_name}_dir")
        candidate = Path(root) / "nested" / "object.bin"

        backend._assert_no_managed_escape(candidate)
        assert backend.namespace_for(str(candidate)) == f"{root_name}:{root}"

    def test_rejects_a_path_below_a_missing_root(self, tmp_path: Path) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import _overlay, settings

        missing = tmp_path / "unmounted"
        _overlay["data_dir"] = missing
        with pytest.raises(StorageConfigurationError, match="root_unavailable"):
            backend._assert_no_managed_escape(missing / "object.bin")
        assert not missing.exists()
        # Keep the second configured root exercised after changing the overlay.
        assert Path(settings.thumb_dir).is_dir()

    def test_rejects_a_symlink_that_escapes_a_managed_root(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        outside = tmp_path / "outside"
        outside.mkdir()
        link = Path(settings.data_dir) / "escape"
        link.symlink_to(outside, target_is_directory=True)

        with pytest.raises(StorageCollisionError, match="symlink_escape"):
            backend._assert_no_managed_escape(link / "object.bin")

    def test_opens_a_pinned_parent_without_following_a_child_symlink(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        destination = Path(settings.data_dir) / "nested" / "object.bin"
        pinned = backend._open_pinned_parent(destination)
        assert pinned is not None
        root_fd, parent_fd, name, root, role = pinned
        try:
            assert (name, root, role) == ("object.bin", Path(settings.data_dir), "data")
        finally:
            import os

            os.close(parent_fd)
            os.close(root_fd)

        # The existing directory path takes the FileExistsError branch of the
        # descriptor-relative parent walk on a second publication.
        pinned_again = backend._open_pinned_parent(destination)
        assert pinned_again is not None
        os.close(pinned_again[1])
        os.close(pinned_again[0])

    def test_reports_a_changed_pinned_root(self, tmp_path: Path) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        root = Path(settings.data_dir)
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            real_stat = os.stat

            def changed_stat(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == root and kwargs.get("follow_symlinks") is False:
                    return type(result)(
                        (result.st_mode, result.st_ino + 1, result.st_dev)
                        + tuple(result)[3:]
                    )
                return result

            # A root replacement is represented by a changed identity at the
            # pathname; no write is attempted by this probe.
            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr("app.services.storage_backend.os.stat", changed_stat)
            try:
                with pytest.raises(StorageConfigurationError, match="root_changed"):
                    backend._assert_pinned_root_current(fd, root)
            finally:
                monkeypatch.undo()
        finally:
            os.close(fd)

    def test_records_invalid_root_binding_json(self, tmp_path: Path) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        marker = Path(settings.data_dir) / ".printstash-storage-root.json"
        marker.write_text("{not-json", encoding="utf-8")

        assert backend._bind_root("data", Path(settings.data_dir)) is False
        assert backend._root_binding_diagnostics["data"] == "binding_invalid"

    def test_rejects_mutation_when_root_binding_disappears(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        root = Path(settings.data_dir)
        marker = root / ".printstash-storage-root.json"
        marker.unlink()

        with pytest.raises(StorageConfigurationError, match="root_unavailable"):
            backend._assert_root_binding_for(root / "missing.bin")

    @pytest.mark.parametrize(
        "rename_error",
        [
            pytest.param(FileNotFoundError, id="missing"),
            pytest.param(OSError, id="io"),
        ],
    )
    def test_reclaim_preserves_bytes_when_quarantine_rename_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        rename_error: type[OSError],
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "reclaim.bin"
        key.write_bytes(b"payload")

        def fail_rename(*_args: object, **_kwargs: object) -> None:
            raise rename_error("rename failed")

        monkeypatch.setattr("app.services.storage_backend.os.rename", fail_rename)

        result = backend.reclaim_unverified(
            str(key), expected_size=len(b"payload"), expected_etag=None
        )

        assert result is (rename_error is FileNotFoundError)
        assert key.read_bytes() == b"payload"

    def test_reclaim_declines_a_non_regular_object(self, tmp_path: Path) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "directory"
        key.mkdir()

        assert (
            backend.reclaim_unverified(
                str(key), expected_size=key.stat().st_size, expected_etag=None
            )
            is False
        )

    def test_reclaim_declines_when_the_open_inode_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "changing.bin"
        key.write_bytes(b"payload")
        real_fstat = os.fstat
        calls = 0

        def changing_fstat(fd: int):
            nonlocal calls
            calls += 1
            result = real_fstat(fd)
            if calls == 2:
                values = list(result)
                values[6] += 1
                return os.stat_result(values)
            return result

        monkeypatch.setattr("app.services.storage_backend.os.fstat", changing_fstat)
        assert (
            backend.reclaim_unverified(
                str(key), expected_size=len(b"payload"), expected_etag=None
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_quarantine_restores_when_second_identity_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "quarantine.bin"
        receipt = backend.create_bytes(b"payload", str(key))
        checks = 0

        def first_check_only(_receipt: CreationReceipt) -> bool:
            nonlocal checks
            checks += 1
            return checks == 1

        monkeypatch.setattr(backend, "creation_matches", first_check_only)

        def fail_restore_link(*_args: object, **_kwargs: object) -> None:
            raise OSError("link unavailable")

        monkeypatch.setattr("app.services.storage_backend.os.link", fail_restore_link)
        with pytest.raises(OSError, match="link unavailable"):
            backend._quarantine_owned(receipt)
        assert not key.exists()
        assert list(key.parent.glob(".printstash-quarantine-*"))

    def test_publishes_through_the_guarded_hardlinkless_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "guarded.bin"

        def no_hardlinks(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.EXDEV, "cross-device")

        monkeypatch.setattr("app.services.storage_backend.os.link", no_hardlinks)
        receipt = backend.create_bytes(b"guarded", str(key))

        assert receipt.device is None
        assert receipt.inode is None
        assert key.read_bytes() == b"guarded"

    def test_publishes_an_unbound_backup_path_through_guarded_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"

        def no_hardlinks(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.EXDEV, "cross-device")

        monkeypatch.setattr("app.services.storage_backend.os.link", no_hardlinks)
        monkeypatch.setattr(
            "app.services.storage_backend._fsync_directory",
            lambda _path: None,
        )
        receipt = backend.create_bytes(b"backup", str(key))

        assert receipt.device is None
        assert key.read_bytes() == b"backup"

    def test_preserves_an_unsupported_backup_publication_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"

        def unsupported_link(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.EIO, "link failed")

        monkeypatch.setattr("app.services.storage_backend.os.link", unsupported_link)
        with pytest.raises(OSError, match="link failed"):
            backend.create_bytes(b"backup", str(key))
        assert not key.exists()

    def test_reports_a_backup_collision_after_hardlink_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"
        key.write_bytes(b"existing")
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "cross-device")
            ),
        )
        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"backup", str(key))
        assert key.read_bytes() == b"existing"

    def test_retains_an_uncertain_backup_after_copy_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "cross-device")
            ),
        )

        def fail_copy(*_args: object, **_kwargs: object) -> None:
            raise OSError("copy failed")

        monkeypatch.setattr(
            "app.services.storage_backend.shutil.copyfileobj", fail_copy
        )
        with pytest.raises(OSError, match="copy failed"):
            backend.create_bytes(b"backup", str(key))
        assert key.exists()

    def test_accepts_backup_publication_when_directory_fsync_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "cross-device")
            ),
        )
        monkeypatch.setattr(
            "app.services.storage_backend._fsync_directory",
            lambda _path: (_ for _ in ()).throw(OSError("fsync failed")),
        )
        receipt = backend.create_bytes(b"backup", str(key))
        assert receipt.size == 6
        assert key.read_bytes() == b"backup"

    def test_assigns_a_fallback_namespace_for_an_application_staging_path(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        key = tmp_path / "staging" / "archive.tar"

        receipt = backend.create_bytes(b"archive", str(key))

        assert receipt.namespace == f"external:{key.parent.resolve()}"

    def test_keeps_a_successful_write_when_temp_cleanup_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.backup_dir) / "backup.tar"
        real_unlink = Path.unlink

        def cleanup_race(path: Path, *args: object, **kwargs: object) -> None:
            if path.name.startswith(".printstash-create-") and not path.exists():
                raise OSError("cleanup unavailable")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", cleanup_race)
        receipt = backend.create_bytes(b"backup", str(key))

        assert receipt.size == 6
        assert key.read_bytes() == b"backup"

    def test_keeps_a_download_when_download_temp_cleanup_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = LocalStorageBackend()
        source = tmp_path / "source.bin"
        destination = tmp_path / "destination.bin"
        source.write_bytes(b"payload")
        real_unlink = Path.unlink

        def cleanup_failure(path: Path, *args: object, **kwargs: object) -> None:
            if path.name.startswith(".printstash-download-"):
                raise OSError("cleanup unavailable")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", cleanup_failure)
        assert backend.download_to_path(str(source), destination) == destination
        assert destination.read_bytes() == b"payload"

    def test_refuses_replacement_when_the_adapter_is_not_verified(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        backend._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.INODE,
            verified_delete=True,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=True,
        )
        with pytest.raises(NotImplementedError, match="atomic_replace"):
            backend.replace_bytes(
                b"payload",
                CreationReceipt(
                    key=str(Path(settings.data_dir) / "missing.bin"),
                    size=0,
                    token="token",
                    backend="local",
                    namespace="data",
                ),
            )

    def test_creation_matching_fails_when_inode_identity_is_unavailable(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        backend._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=True,
        )
        assert (
            backend.creation_matches(
                CreationReceipt(
                    key="anything",
                    size=0,
                    token="token",
                    backend="local",
                    namespace="local",
                )
            )
            is False
        )

    def test_skips_rollback_when_delete_identity_is_not_verified(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        backend._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.INODE,
            verified_delete=False,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=True,
        )

        assert (
            backend.rollback_create(
                CreationReceipt(
                    key="key",
                    size=0,
                    token="token",
                    backend="local",
                    namespace="local",
                )
            )
            is False
        )

    def test_preserves_a_quarantine_when_identity_changes_before_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        quarantine = tmp_path / "quarantine.bin"
        quarantine.write_bytes(b"preserve")
        receipt = CreationReceipt(
            key=str(tmp_path / "destination.bin"),
            size=7,
            token="token",
            backend="local",
            namespace="local",
        )
        monkeypatch.setattr(backend, "_quarantine_owned", lambda _receipt: quarantine)
        monkeypatch.setattr(backend, "creation_matches", lambda _receipt: False)

        assert backend.rollback_create(receipt) is False
        assert quarantine.read_bytes() == b"preserve"

    def test_preserves_the_old_quarantine_when_replacement_collides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "replace.bin"
        receipt = backend.create_bytes(b"old", str(key))

        def claim_destination_then_collide(*_args: object, **_kwargs: object) -> None:
            key.write_bytes(b"concurrent")
            raise FileExistsError(str(key))

        monkeypatch.setattr(
            "app.services.storage_backend.os.link", claim_destination_then_collide
        )
        with pytest.raises(StorageCollisionError):
            backend.replace_bytes(b"new", receipt)
        assert key.read_bytes() == b"concurrent"
        assert list(key.parent.glob(".printstash-quarantine-*"))

    def test_keeps_an_old_quarantine_when_its_cleanup_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "replace.bin"
        receipt = backend.create_bytes(b"old", str(key))
        real_unlink = Path.unlink

        def fail_quarantine_cleanup(
            path: Path, *args: object, **kwargs: object
        ) -> None:
            if path.name.startswith(".printstash-quarantine-"):
                raise OSError("quarantine cleanup failed")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_quarantine_cleanup)
        replacement = backend.replace_bytes(b"new", receipt)
        assert Path(replacement.key).read_bytes() == b"new"
        assert list(key.parent.glob(".printstash-quarantine-*"))

    def test_declines_adoption_when_the_verified_inode_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "legacy.stl"
        key.write_bytes(b"legacy")
        real_fstat = os.fstat
        calls = 0

        def changing_fstat(fd: int):
            nonlocal calls
            calls += 1
            result = real_fstat(fd)
            if calls == 2:
                values = list(result)
                values[6] += 1
                return os.stat_result(values)
            return result

        monkeypatch.setattr("app.services.storage_backend.os.fstat", changing_fstat)
        with pytest.raises(StorageCollisionError, match="object_changed"):
            backend.adopt_existing(
                str(key),
                expected_size=6,
                expected_sha256=hashlib.sha256(b"legacy").hexdigest(),
            )

    def test_uses_the_base_probe_contract_for_opaque_local_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        monkeypatch.setattr(backend, "direct_path", lambda _key: None)

        with pytest.raises(NotImplementedError, match="destructive_access_probe"):
            backend.verify_destructive_access(["opaque-key"])

    def test_usage_skips_a_file_that_disappears_during_stat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        root = tmp_path / "inventory"
        root.mkdir()
        file = root / "disappearing.bin"
        file.write_bytes(b"payload")
        real_stat = Path.stat
        stat_calls = 0

        def missing_stat(path: Path, *args: object, **kwargs: object):
            nonlocal stat_calls
            if path == file:
                stat_calls += 1
                if stat_calls == 2:
                    raise OSError("file disappeared")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", missing_stat)
        assert backend.usage(str(root))["object_count"] == 0

    def test_rejects_an_unusable_hardlink_publication_on_a_managed_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "unsupported.bin"
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EIO, "link failed")
            ),
        )
        with pytest.raises(OSError, match="link failed"):
            backend.create_bytes(b"payload", str(key))
        assert not key.exists()

    def test_reports_a_managed_root_collision_after_hardlink_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "collision.bin"
        key.write_bytes(b"existing")
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "cross-device")
            ),
        )
        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"payload", str(key))
        assert key.read_bytes() == b"existing"

    def test_retains_an_uncertain_managed_root_after_copy_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import errno

        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "uncertain.bin"
        monkeypatch.setattr(
            "app.services.storage_backend.os.link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EXDEV, "cross-device")
            ),
        )
        monkeypatch.setattr(
            "app.services.storage_backend.shutil.copyfileobj",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
        )
        with pytest.raises(OSError, match="copy failed"):
            backend.create_bytes(b"payload", str(key))
        assert key.exists()

    def test_refuses_a_write_when_local_identity_is_unverified(
        self, tmp_path: Path
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        backend._capabilities = StorageCapabilities(
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=True,
        )
        with pytest.raises(StorageConfigurationError, match="storage_write_unverified"):
            backend.create_bytes(b"payload", str(tmp_path / "files" / "blocked.bin"))

    def test_delegates_opaque_local_writes_to_the_base_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        monkeypatch.setattr(backend, "direct_path", lambda _key: None)

        with pytest.raises(NotImplementedError, match="atomic_create"):
            backend.create_bytes(b"payload", "opaque-key")

    def test_declines_adoption_when_receipt_revalidation_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _configure_local_storage(tmp_path)
        from app.core.config import settings

        key = Path(settings.data_dir) / "legacy.stl"
        key.write_bytes(b"legacy")
        monkeypatch.setattr(backend, "creation_matches", lambda _receipt: False)
        with pytest.raises(StorageCollisionError, match="object_changed"):
            backend.adopt_existing(
                str(key),
                expected_size=6,
                expected_sha256=hashlib.sha256(b"legacy").hexdigest(),
            )


class TestLegacyRootEnrollmentBranches:
    def test_rejects_a_root_that_is_not_present(self, tmp_path: Path) -> None:
        assert not enroll_legacy_local_root(
            tmp_path / "missing", role="data", installation="install", proofs=[]
        )

    def test_accepts_an_already_current_binding(self, tmp_path: Path) -> None:
        root = tmp_path / "files"
        root.mkdir()
        (root / ".printstash-storage-root.json").write_text(
            '{"format":1,"installation":"install","role":"data"}',
            encoding="utf-8",
        )

        assert enroll_legacy_local_root(
            root, role="data", installation="install", proofs=[]
        )

    def test_rejects_a_binding_for_another_installation(self, tmp_path: Path) -> None:
        root = tmp_path / "files"
        root.mkdir()
        (root / ".printstash-storage-root.json").write_text(
            '{"installation":"other","role":"data"}', encoding="utf-8"
        )

        assert not enroll_legacy_local_root(
            root, role="data", installation="install", proofs=[]
        )

    def test_rejects_malformed_binding_json(self, tmp_path: Path) -> None:
        root = tmp_path / "files"
        root.mkdir()
        (root / ".printstash-storage-root.json").write_text("{", encoding="utf-8")

        assert not enroll_legacy_local_root(
            root, role="data", installation="install", proofs=[]
        )

    def test_requires_proof_for_a_nonempty_markerless_root(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "files"
        root.mkdir()
        (root / "existing.stl").write_bytes(b"legacy")

        assert not enroll_legacy_local_root(
            root, role="data", installation="install", proofs=[]
        )

    def test_allows_an_explicitly_empty_new_root(self, tmp_path: Path) -> None:
        root = tmp_path / "files"
        root.mkdir()

        assert enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[],
            allow_empty=True,
        )

    def test_accepts_a_hash_proven_legacy_file(self, tmp_path: Path) -> None:
        import hashlib

        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")

        assert enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, hashlib.sha256(b"legacy").hexdigest())],
        )

    def test_rejects_a_proof_without_a_valid_hash(self, tmp_path: Path) -> None:
        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")

        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, "not-a-hash")],
        )

    @pytest.mark.parametrize(
        "proof",
        [
            pytest.param(("root", 0, "0" * 64), id="root-itself"),
            pytest.param(("file", 999, "0" * 64), id="size-mismatch"),
            pytest.param(("file", 6, "0" * 64), id="digest-mismatch"),
            pytest.param(("missing", 1, "0" * 64), id="missing-file"),
        ],
    )
    def test_rejects_an_invalid_legacy_proof(
        self, tmp_path: Path, proof: tuple[str, int, str]
    ) -> None:
        import hashlib

        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")
        names = {"root": root, "file": payload, "missing": root / "missing.stl"}
        path, size, digest = proof
        if path == "root":
            candidate = names[path]
        else:
            candidate = names[path]
        # Keep the expected digest expression visible in this matrix: only the
        # correctly hashed file can be enrolled.
        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(candidate, size, digest)],
        )
        assert hashlib.sha256(b"legacy").hexdigest() != digest or path != "file"

    def test_upgrades_a_legacy_marker_after_proof_validation(
        self, tmp_path: Path
    ) -> None:
        import hashlib

        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")
        marker = root / ".printstash-storage-root.json"
        marker.write_text('{"installation":"install","role":"data"}', encoding="utf-8")

        assert enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, hashlib.sha256(b"legacy").hexdigest())],
        )
        assert '"format":1' in marker.read_text(encoding="utf-8")

    def test_aborts_when_legacy_binding_changes_during_proofing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")
        marker = root / ".printstash-storage-root.json"
        marker.write_text('{"installation":"install","role":"data"}', encoding="utf-8")
        real_read_text = Path.read_text
        reads = 0

        def changed_marker(path: Path, *args: object, **kwargs: object) -> str:
            nonlocal reads
            value = real_read_text(path, *args, **kwargs)
            if path == marker:
                reads += 1
                if reads == 1:
                    marker.write_text(
                        '{"installation":"other","role":"data"}', encoding="utf-8"
                    )
            return value

        monkeypatch.setattr(Path, "read_text", changed_marker)
        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, "0" * 64)],
        )

    def test_aborts_when_legacy_binding_changes_after_valid_proof(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")
        marker = root / ".printstash-storage-root.json"
        marker.write_text('{"installation":"install","role":"data"}', encoding="utf-8")
        real_read_text = Path.read_text
        reads = 0

        def changed_marker(path: Path, *args: object, **kwargs: object) -> str:
            nonlocal reads
            value = real_read_text(path, *args, **kwargs)
            if path == marker:
                reads += 1
                if reads == 1:
                    marker.write_text(
                        '{"installation":"other","role":"data"}', encoding="utf-8"
                    )
            return value

        monkeypatch.setattr(Path, "read_text", changed_marker)
        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, hashlib.sha256(b"legacy").hexdigest())],
        )

    def test_aborts_when_legacy_binding_becomes_invalid_during_upgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload.write_bytes(b"legacy")
        marker = root / ".printstash-storage-root.json"
        marker.write_text('{"installation":"install","role":"data"}', encoding="utf-8")
        real_read_text = Path.read_text
        reads = 0

        def invalid_marker(path: Path, *args: object, **kwargs: object) -> str:
            nonlocal reads
            value = real_read_text(path, *args, **kwargs)
            if path == marker:
                reads += 1
                if reads == 1:
                    marker.write_text("{", encoding="utf-8")
            return value

        monkeypatch.setattr(Path, "read_text", invalid_marker)
        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, hashlib.sha256(b"legacy").hexdigest())],
        )

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(FileExistsError, id="marker-collision"),
            pytest.param(OSError, id="marker-write"),
        ],
    )
    def test_preserves_legacy_root_when_marker_upgrade_write_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure: type[OSError],
    ) -> None:
        root = tmp_path / "files"
        root.mkdir()
        payload = root / "legacy.stl"
        payload_bytes = b"legacy"
        payload.write_bytes(payload_bytes)
        marker = root / ".printstash-storage-root.json"
        marker_contents = '{"installation":"install","role":"data"}'
        marker.write_text(marker_contents, encoding="utf-8")
        real_open = Path.open

        def fail_temporary_marker(
            path: Path, mode: str = "r", *args: object, **kwargs: object
        ):
            if mode == "x" and path.name.startswith("..printstash-storage-root.json"):
                raise failure("marker write failed")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_temporary_marker)
        assert not enroll_legacy_local_root(
            root,
            role="data",
            installation="install",
            proofs=[(payload, 6, hashlib.sha256(b"legacy").hexdigest())],
        )
        assert payload.read_bytes() == payload_bytes
        assert marker.read_text(encoding="utf-8") == marker_contents
