"""Local storage writes publish atomically and retain ownership evidence.

Create-only publication protects concurrent uploads, while replacement and
rollback preserve bytes when a destination changes during the operation.

A staged import becomes its library object by hard link whenever staging shares
the library's mount, so publishing a large file costs no copy and no second
allocation. Anything that cannot be linked safely still arrives, by copy.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from io import BytesIO
from pathlib import Path
from threading import Barrier, Thread
from typing import Iterator

import pytest

import app.modules.storage.storage_backend.local as storage_local
from app.modules.storage.storage_backend.contracts import (
    StorageCollisionError,
    StorageConfigurationError,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend

STAGED_BYTES = b"solid staged-part"


def _staged(tmp_path: Path, name: str = "part.stl") -> Path:
    staged = tmp_path / "staging" / name
    staged.write_bytes(STAGED_BYTES)
    return staged


@pytest.fixture
def cross_mount_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """link(2) out of staging fails the way it does across two mounts."""
    staging = tmp_path / "staging"
    real_link = os.link

    def link(src, dst, *args, **kwargs):
        if Path(os.fsdecode(src)).parent == staging and (
            Path(os.fsdecode(dst)).parent != staging
            or kwargs.get("dst_dir_fd") is not None
        ):
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), os.fsdecode(src))
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(storage_local.os, "link", link)


@pytest.fixture
def unreleasable_staged(tmp_path: Path) -> Iterator[Path]:
    """A staged file that can be linked from but not removed: a real fault."""
    staged = _staged(tmp_path)
    staged.parent.chmod(0o500)
    yield staged
    staged.parent.chmod(0o700)


class TestReplaceStream:
    def test_rejects_a_pre_format_binding_without_implicit_enrollment(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        marker = tmp_path / "files" / ".printstash-storage-root.json"
        marker.write_text(
            '{"installation":"%s","role":"data"}' % ("a" * 64),
            encoding="utf-8",
        )
        destination = tmp_path / "files" / "legacy-marker.stl"

        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.create_bytes(b"must-not-enroll", str(destination))

        assert not destination.exists()

    def test_sentinel_loss_blocks_all_mutations_but_keeps_reads(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "sentinel-loss.stl"
        receipt = configured_backend.create_bytes(b"owned", str(destination))
        marker = tmp_path / "files" / ".printstash-storage-root.json"
        marker.unlink()

        assert configured_backend.read_bytes(str(destination)) == b"owned"
        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.create_bytes(b"new", str(tmp_path / "files" / "new.stl"))
        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.replace_bytes(b"replacement", receipt)
        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.rollback_create(receipt)
        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.adopt_existing(
                str(destination), expected_size=5, expected_sha256="0" * 64
            )
        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.verify_destructive_access([str(destination)])
        assert destination.read_bytes() == b"owned"

    def test_explicit_replace_requires_current_creation_receipt(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "thumbnail.webp"
        receipt = configured_backend.create_bytes(b"first", str(destination))

        replacement = configured_backend.replace_bytes(b"second", receipt)

        assert destination.read_bytes() == b"second"
        assert configured_backend.creation_matches(replacement)
        with pytest.raises(StorageCollisionError):
            configured_backend.replace_bytes(b"stale", receipt)
        assert destination.read_bytes() == b"second"

    def test_rollback_race_after_quarantine_preserves_new_destination(
        self, configured_backend: LocalStorageBackend, tmp_path: Path, monkeypatch
    ) -> None:
        destination = tmp_path / "files" / "part.stl"
        receipt = configured_backend.create_bytes(b"owned", str(destination))
        real_replace = storage_local.os.replace

        def raced_replace(source, target):
            real_replace(source, target)
            Path(source).write_bytes(b"new-user-file")

        monkeypatch.setattr(storage_local.os, "replace", raced_replace)

        assert configured_backend.rollback_create(receipt) is True
        assert destination.read_bytes() == b"new-user-file"


class TestCreateOnlyWrites:
    """Two writers reaching the same key, and the one that must lose.

    Create-only is the whole safety model: a write that would land on an existing
    object raises instead of overwriting it, so two ingests that dedup to the
    same key cannot destroy each other's bytes. These pin the loser's side —
    the failed writer leaves nothing behind, including no partial file.
    """

    def test_two_concurrent_create_only_writes_have_one_winner(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "race.bin"
        barrier = Barrier(3)
        outcomes: list[str] = []

        def write(payload: bytes) -> None:
            barrier.wait(timeout=5)
            try:
                LocalStorageBackend().create_bytes(payload, str(destination))
                outcomes.append("created")
            except StorageCollisionError:
                outcomes.append("collision")

        threads = [Thread(target=write, args=(value,)) for value in (b"one", b"two")]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        assert sorted(outcomes) == ["collision", "created"]
        assert destination.read_bytes() in {b"one", b"two"}

    def test_managed_create_rejects_symlink_escape(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        escaped = tmp_path / "files" / "escaped"
        escaped.symlink_to(outside, target_is_directory=True)

        with pytest.raises(
            StorageCollisionError, match="managed_storage_symlink_escape"
        ):
            configured_backend.create_bytes(
                b"must-not-escape", str(escaped / "part.stl")
            )

        assert not (outside / "part.stl").exists()

    def test_refuses_an_unchecked_move_without_touching_the_source(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        src = tmp_path / "staged.stl"
        src.write_bytes(b"solid")
        dest = tmp_path / "nested" / "dir" / "final.stl"

        with pytest.raises(RuntimeError, match="unchecked_storage_move_disabled"):
            configured_backend.move(str(src), str(dest))

        assert src.read_bytes() == b"solid"
        assert not dest.exists()

    def test_rollback_receipt_cannot_delete_a_replaced_destination(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "part.stl"
        receipt = configured_backend.create_bytes(b"created", str(destination))
        destination.unlink()
        destination.write_bytes(b"replacement")

        assert configured_backend.rollback_create(receipt) is False
        assert destination.read_bytes() == b"replacement"


class TestCreateStream:
    def test_external_root_descendants_publish_through_pinned_parent(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "nas"
        root.mkdir()
        backend = LocalStorageBackend(external_roots=(root,))

        backend.create_bytes(b"external", str(root / "collections" / "part.stl"))

        assert (root / "collections" / "part.stl").read_bytes() == b"external"

    def test_external_mount_loss_before_pin_never_recreates_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "nas"
        root.mkdir()
        backend = LocalStorageBackend(external_roots=(root,))
        real_open = storage_local.os.open

        def drop_root_before_open(path, *args, **kwargs):
            if isinstance(path, (str, bytes, Path)) and Path(path) == root:
                root.rmdir()
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(storage_local.os, "open", drop_root_before_open)

        with pytest.raises(FileNotFoundError):
            backend.create_bytes(b"must-not-recreate", str(root / "part.stl"))

        assert not root.exists()

    def test_external_replacement_after_path_check_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "nas"
        root.mkdir()
        expected = {
            "format": 1,
            "installation": "a" * 64,
            "role": "external-library",
            "library_id": 7,
            "root_identity": "b" * 64,
        }
        (root / ".printstash-external-root.json").write_text(
            json.dumps(expected), encoding="utf-8"
        )
        replacement = tmp_path / "replacement"
        replacement.mkdir()
        foreign = {**expected, "library_id": 8, "root_identity": "c" * 64}
        (replacement / ".printstash-external-root.json").write_text(
            json.dumps(foreign), encoding="utf-8"
        )
        backend = LocalStorageBackend(
            external_roots=(root,), external_root_bindings={root: expected}
        )
        real_open = storage_local.os.open
        swapped = False

        def swap_before_root_open(path, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and isinstance(path, (str, bytes, Path))
                and Path(path) == root
            ):
                swapped = True
                old = tmp_path / "old-mount"
                root.rename(old)
                replacement.rename(root)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(storage_local.os, "open", swap_before_root_open)

        with pytest.raises(
            StorageConfigurationError, match="external_root_binding_changed"
        ):
            backend.create_bytes(b"must-not-publish", str(root / "part.stl"))

        assert not (root / "part.stl").exists()

    def test_mount_marker_swap_during_publication_fails_closed(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        destination = tmp_path / "files" / "mount-swap.stl"
        marker = tmp_path / "files" / ".printstash-storage-root.json"
        real_link = storage_local.os.link

        def swap_marker_before_publication(source, target, **kwargs):
            marker.write_text(
                '{"format":1,"installation":"wrong-mount","role":"data"}',
                encoding="utf-8",
            )
            return real_link(source, target, **kwargs)

        monkeypatch.setattr(storage_local.os, "link", swap_marker_before_publication)

        with pytest.raises(StorageConfigurationError, match="storage_root_changed"):
            configured_backend.create_bytes(b"must-reconcile", str(destination))

        # The descriptor-relative write never reaches another root.  The
        # published bytes remain available for ownership reconciliation after
        # the failed acknowledgement.
        assert destination.read_bytes() == b"must-reconcile"

    def test_returns_a_receipt_for_the_bytes_it_wrote(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "model" / "v1" / "part.stl"

        receipt = configured_backend.create_stream(BytesIO(b"owned"), str(destination))

        assert (receipt.key, receipt.size) == (str(destination), 5)
        assert destination.read_bytes() == b"owned"

    def test_refuses_a_second_write_to_the_same_key(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "model" / "v1" / "part.stl"
        configured_backend.create_stream(BytesIO(b"owned"), str(destination))

        with pytest.raises(StorageCollisionError):
            configured_backend.create_stream(BytesIO(b"attacker"), str(destination))

        assert destination.read_bytes() == b"owned"

    def test_failed_create_stream_never_publishes_partial_destination(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "partial.bin"

        class FailingStream:
            calls = 0

            def read(self, _size: int) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("source failed")

        with pytest.raises(OSError, match="source failed"):
            configured_backend.create_stream(FailingStream(), str(destination))

        assert not destination.exists()


class TestMoveIn:
    def test_publishes_a_staged_file_by_hard_link(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        staged = _staged(tmp_path)
        staged_inode = staged.stat().st_ino
        destination = tmp_path / "files" / "model" / "v1" / "part.stl"

        configured_backend.move_in(staged, str(destination))

        assert destination.stat().st_ino == staged_inode

    def test_releases_the_staged_name(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        staged = _staged(tmp_path)

        configured_backend.move_in(staged, str(tmp_path / "files" / "part.stl"))

        assert not staged.exists()

    def test_returns_a_receipt_that_proves_the_linked_inode(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        # Taken after the staged name is gone: releasing it changes the ctime.
        staged = _staged(tmp_path)

        receipt = configured_backend.move_in(
            staged, str(tmp_path / "files" / "part.stl")
        )

        assert configured_backend.creation_matches(receipt)

    def test_publishes_a_linked_object_private_to_the_app(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        # A link keeps its inode's mode; a created object is 0600, so a staged
        # file written world-readable must not widen what the library exposes.
        staged = _staged(tmp_path)
        staged.chmod(0o644)
        destination = tmp_path / "files" / "part.stl"

        configured_backend.move_in(staged, str(destination))

        assert stat.S_IMODE(destination.stat().st_mode) == 0o600

    def test_reports_the_staged_size(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        staged = _staged(tmp_path)

        receipt = configured_backend.move_in(
            staged, str(tmp_path / "files" / "part.stl")
        )

        assert receipt.size == len(STAGED_BYTES)

    def test_hard_links_into_an_unmanaged_directory(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        # Backup archives have no enrolled root, and gain the same zero-copy
        # publication from a temp file built beside them.
        staged = _staged(tmp_path)
        staged_inode = staged.stat().st_ino
        destination = tmp_path / "backups" / "printstash-backup.tar.gz"

        configured_backend.move_in(staged, str(destination))

        assert destination.stat().st_ino == staged_inode

    def test_copies_a_staged_file_from_another_mount(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        cross_mount_staging: None,
    ) -> None:
        staged = _staged(tmp_path)
        destination = tmp_path / "files" / "part.stl"

        configured_backend.move_in(staged, str(destination))

        assert destination.read_bytes() == STAGED_BYTES

    def test_consumes_a_staged_file_it_copied(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        cross_mount_staging: None,
    ) -> None:
        staged = _staged(tmp_path)

        configured_backend.move_in(staged, str(tmp_path / "files" / "part.stl"))

        assert not staged.exists()

    def test_never_aliases_a_staged_file_it_cannot_release(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        unreleasable_staged: Path,
    ) -> None:
        # A surviving second name would let a write through staging change
        # library bytes, and its later removal would break the receipt's ctime.
        destination = tmp_path / "files" / "part.stl"

        configured_backend.move_in(unreleasable_staged, str(destination))

        assert destination.stat().st_ino != unreleasable_staged.stat().st_ino

    def test_keeps_a_staged_file_it_cannot_release(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        unreleasable_staged: Path,
    ) -> None:
        configured_backend.move_in(
            unreleasable_staged, str(tmp_path / "files" / "part.stl")
        )

        assert unreleasable_staged.read_bytes() == STAGED_BYTES

    def test_returns_creation_proof_for_a_staged_file_it_cannot_release(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        unreleasable_staged: Path,
    ) -> None:
        receipt = configured_backend.move_in(
            unreleasable_staged, str(tmp_path / "files" / "part.stl")
        )

        assert configured_backend.creation_matches(receipt)

    def test_publishes_the_bytes_behind_a_staged_symlink(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        target = _staged(tmp_path, name="real.stl")
        staged = tmp_path / "staging" / "part.stl"
        staged.symlink_to(target)
        destination = tmp_path / "files" / "part.stl"

        configured_backend.move_in(staged, str(destination))

        assert (destination.is_symlink(), destination.read_bytes()) == (
            False,
            STAGED_BYTES,
        )

    def test_never_publishes_a_symlink_swapped_in_for_the_staged_file(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        staged = _staged(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"not a staged upload")
        real_link = os.link

        def swap_then_link(src, dst, *args, **kwargs):
            if Path(os.fsdecode(src)) == staged:
                staged.unlink()
                staged.symlink_to(outside)
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(storage_local.os, "link", swap_then_link)
        destination = tmp_path / "files" / "part.stl"

        configured_backend.move_in(staged, str(destination))

        assert not destination.is_symlink()

    def test_refuses_to_replace_an_existing_object(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        destination = tmp_path / "files" / "part.stl"
        destination.write_bytes(b"owned")

        with pytest.raises(StorageCollisionError):
            configured_backend.move_in(_staged(tmp_path), str(destination))

        assert destination.read_bytes() == b"owned"

    def test_keeps_the_staged_file_after_a_collision(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        staged = _staged(tmp_path)
        destination = tmp_path / "files" / "part.stl"
        destination.write_bytes(b"owned")

        with pytest.raises(StorageCollisionError):
            configured_backend.move_in(staged, str(destination))

        assert staged.read_bytes() == STAGED_BYTES

    def test_refuses_a_library_root_that_lost_its_enrollment(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        staged = _staged(tmp_path)
        (tmp_path / "files" / ".printstash-storage-root.json").unlink()

        with pytest.raises(StorageConfigurationError, match="storage_root_unavailable"):
            configured_backend.move_in(staged, str(tmp_path / "files" / "part.stl"))

        assert staged.read_bytes() == STAGED_BYTES


class TestEnsureSetup:
    def test_reports_zero_copy_imports_when_staging_shares_the_library_mount(
        self, configured_backend: LocalStorageBackend
    ) -> None:
        configured_backend.ensure_setup()

        assert configured_backend.probe_diagnostics["staged_hardlink"] is True

    def test_reports_copying_imports_when_staging_is_on_another_mount(
        self, configured_backend: LocalStorageBackend, cross_mount_staging: None
    ) -> None:
        configured_backend.ensure_setup()

        assert configured_backend.probe_diagnostics["staged_hardlink"] is False

    def test_warns_that_imports_will_copy_every_file(
        self,
        configured_backend: LocalStorageBackend,
        cross_mount_staging: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        configured_backend.ensure_setup()

        assert "imports copy every staged file" in caplog.text

    def test_leaves_no_probe_behind(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        configured_backend.ensure_setup()

        assert [
            path.name
            for root in ("staging", "files")
            for path in (tmp_path / root).iterdir()
            if "probe" in path.name
        ] == []


class TestStagingProbe:
    def test_reports_staging_capabilities(self, configured_backend, tmp_path) -> None:
        configured_backend.ensure_setup()
        probe = configured_backend.probe_diagnostics["staging"]
        assert probe == {
            "role": "staging",
            "path": str(tmp_path / "staging"),
            "fs_kind": "local",
            "hardlink": True,
            "exclusive_create": True,
            "directory_fsync": True,
        }

    def test_warns_once_for_hardlinkless_staging(
        self, configured_backend, tmp_path, monkeypatch, caplog
    ) -> None:
        real_link = os.link

        def no_staging_links(src, dst, *args, **kwargs):
            if Path(src).parent == tmp_path / "staging":
                raise OSError(errno.EPERM, "no hard links")
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "link", no_staging_links)
        configured_backend.ensure_setup()
        configured_backend.ensure_setup()
        assert configured_backend.probe_diagnostics["staging"]["hardlink"] is False
        warnings = configured_backend.capabilities.warnings
        assert any("staging" in warning and "copy" in warning for warning in warnings)
        assert (
            sum(
                "staging" in record.message
                and "does not support hard links" in record.message
                for record in caplog.records
            )
            == 1
        )
        assert configured_backend.capabilities.tier.value == "verified"

    @pytest.mark.parametrize("entrypoint", ["create", "move"])
    def test_uses_the_root_copy_capability(
        self, configured_backend, tmp_path, monkeypatch, entrypoint
    ):
        def unsupported(*args, **kwargs):
            raise OSError(errno.EPERM, "no links")

        monkeypatch.setattr(os, "link", unsupported)
        configured_backend.ensure_setup()

        def unexpected_link(*args, **kwargs):
            raise AssertionError("known hardlinkless root must use exclusive copying")

        monkeypatch.setattr(os, "link", unexpected_link)
        destination = tmp_path / "files" / "copied.stl"
        if entrypoint == "create":
            receipt = configured_backend.create_bytes(STAGED_BYTES, str(destination))
        else:
            receipt = configured_backend.move_in(_staged(tmp_path), str(destination))
        assert destination.read_bytes() == STAGED_BYTES
        assert receipt.inode is None

    def test_preserves_verified_library_with_degraded_staging(
        self, configured_backend, tmp_path, monkeypatch
    ):
        real_link = os.link

        def unsupported_staging(src, dst, *args, **kwargs):
            if Path(src).parent == tmp_path / "staging":
                raise OSError(errno.EPERM, "no staging links")
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "link", unsupported_staging)
        configured_backend.ensure_setup()
        destination = tmp_path / "files" / "copied.stl"
        receipt = configured_backend.move_in(_staged(tmp_path), str(destination))
        assert destination.read_bytes() == STAGED_BYTES
        assert receipt.inode == destination.stat().st_ino
        assert configured_backend.capabilities.tier.value == "verified"
