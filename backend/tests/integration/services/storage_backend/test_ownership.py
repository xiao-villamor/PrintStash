"""Deleting or replacing a stored object without ever unlinking the wrong one.

POSIX has no unlink-if-the-inode-still-matches primitive. Checking that a path is the
object we own and *then* unlinking it leaves a window where the path can be replaced, and
what gets deleted is somebody else's file — a newly mounted library, a file another process
just published. The consequence is not a failed request; it is a user's model gone.

So a delete never unlinks the destination. It renames the current inode into a random
same-directory quarantine — atomic, and non-destructive because the only thing it can
overwrite is an empty placeholder this operation just created — then re-proves ownership of
the *moved* inode. Only that moved inode is ever eligible for deletion.

If the second proof fails, the path changed under us. The quarantined inode is put back
with a hard link that **refuses to replace** whatever now occupies the destination, so both
files survive and the operation aborts rather than guessing.

A replacement is the same dance plus a no-replace publication of the new bytes. If that
loses the race, the new object *and* the old quarantined one both survive: it is always
better to leave two files for an operator than to delete one that mattered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import storage_backend as backend_module
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    StorageCollisionError,
)


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalStorageBackend:
    from app.core.config import _overlay

    monkeypatch.setitem(_overlay, "data_dir", tmp_path / "files")
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    return LocalStorageBackend()


@pytest.fixture
def owned(backend: LocalStorageBackend):
    made = {"n": 0}

    def build(data: bytes = b"payload") -> CreationReceipt:
        made["n"] += 1
        key = backend.blob_key("quarantine", made["n"], "object.bin")
        return backend.create_bytes(data, key)

    return build


class TestCreationMatches:
    def test_proves_the_object_it_just_created(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        assert backend.creation_matches(owned()) is True

    def test_refuses_a_receipt_from_another_backend(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()

        # An S3 receipt executed against local disk would name completely
        # different bytes at the same key.
        assert (
            backend.creation_matches(
                CreationReceipt(**{**receipt.__dict__, "backend": "s3"})
            )
            is False
        )

    def test_refuses_an_object_that_is_gone(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        Path(receipt.key).unlink()

        assert backend.creation_matches(receipt) is False

    def test_refuses_a_replacement_at_the_same_path(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        path = Path(receipt.key)
        path.unlink()
        path.write_bytes(b"payload")

        # Same bytes, different inode: not the object the receipt describes.
        assert backend.creation_matches(receipt) is False

    def test_refuses_an_object_whose_size_changed(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        Path(receipt.key).write_bytes(b"payload and more")

        assert backend.creation_matches(receipt) is False

    def test_refuses_a_key_outside_the_root_it_was_recorded_under(
        self, backend: LocalStorageBackend, owned, tmp_path: Path
    ) -> None:
        receipt = owned()

        # The vault may be re-pointed between a write and a delete; a key whose
        # current root is not the recorded one is not ours to touch.
        assert (
            backend.creation_matches(
                CreationReceipt(
                    **{**receipt.__dict__, "namespace": "external:/somewhere/else"}
                )
            )
            is False
        )


class TestRollbackCreate:
    def test_removes_the_object_it_owns(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()

        assert backend.rollback_create(receipt) is True
        assert not Path(receipt.key).exists()

    def test_reports_nothing_removed_when_the_object_is_gone(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        Path(receipt.key).unlink()

        assert backend.rollback_create(receipt) is False

    def test_leaves_a_replacement_alone(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        path = Path(receipt.key)
        path.unlink()
        path.write_bytes(b"somebody else's bytes")

        # The rollback must not delete a file it cannot prove it wrote.
        assert backend.rollback_create(receipt) is False
        assert path.read_bytes() == b"somebody else's bytes"

    def test_leaves_no_quarantine_behind_when_it_declines(
        self, backend: LocalStorageBackend, owned, tmp_path: Path
    ) -> None:
        receipt = owned()
        path = Path(receipt.key)
        path.unlink()
        path.write_bytes(b"somebody else's bytes")

        backend.rollback_create(receipt)

        assert not any(
            entry.name.startswith(".printstash-quarantine-")
            for entry in path.parent.iterdir()
        )


class TestQuarantineOwned:
    def test_hands_back_the_moved_inode(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()

        quarantine = backend._quarantine_owned(receipt)

        assert quarantine is not None
        assert quarantine.read_bytes() == b"payload"
        assert not Path(receipt.key).exists()

    def test_declines_an_object_it_cannot_prove(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        Path(receipt.key).unlink()

        assert backend._quarantine_owned(receipt) is None

    def test_puts_the_inode_back_when_the_second_proof_fails(
        self, backend: LocalStorageBackend, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receipt = owned()
        path = Path(receipt.key)
        calls = {"n": 0}
        real_matches = backend.creation_matches

        def only_the_first_time(candidate: CreationReceipt) -> bool:
            calls["n"] += 1
            return real_matches(candidate) if calls["n"] == 1 else False

        monkeypatch.setattr(backend, "creation_matches", only_the_first_time)

        assert backend._quarantine_owned(receipt) is None
        # Restored, and with its bytes intact.
        assert path.read_bytes() == b"payload"

    def test_raises_when_the_destination_was_taken_while_quarantined(
        self, backend: LocalStorageBackend, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receipt = owned()
        path = Path(receipt.key)
        calls = {"n": 0}
        real_matches = backend.creation_matches

        def only_the_first_time(candidate: CreationReceipt) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return real_matches(candidate)
            # Another process publishes at the destination while our inode is
            # in quarantine, so the restore cannot link back.
            path.write_bytes(b"claimed by somebody else")
            return False

        monkeypatch.setattr(backend, "creation_matches", only_the_first_time)

        # Both files survive and the caller is told, rather than one being lost.
        with pytest.raises(StorageCollisionError):
            backend._quarantine_owned(receipt)
        assert path.read_bytes() == b"claimed by somebody else"


class TestReplaceBytes:
    def test_publishes_the_new_bytes(self, backend: LocalStorageBackend, owned) -> None:
        receipt = owned()

        replacement = backend.replace_bytes(b"new bytes", receipt)

        assert Path(replacement.key).read_bytes() == b"new bytes"

    def test_gives_the_replacement_its_own_receipt(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()

        replacement = backend.replace_bytes(b"new bytes", receipt)

        # A new inode needs a new proof; reusing the old one would authorise a
        # delete of bytes that are no longer there.
        assert replacement.token != receipt.token
        assert backend.creation_matches(replacement) is True

    def test_leaves_no_quarantine_behind(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()

        backend.replace_bytes(b"new bytes", receipt)

        assert not any(
            entry.name.startswith(".printstash-quarantine-")
            for entry in Path(receipt.key).parent.iterdir()
        )

    def test_refuses_to_replace_an_object_it_cannot_prove(
        self, backend: LocalStorageBackend, owned
    ) -> None:
        receipt = owned()
        Path(receipt.key).unlink()

        with pytest.raises(StorageCollisionError):
            backend.replace_bytes(b"new bytes", receipt)


class TestVerifyDestructiveAccess:
    def test_accepts_a_writable_root(self, backend: LocalStorageBackend, owned) -> None:
        receipt = owned()

        backend.verify_destructive_access([receipt.key])

    def test_probes_every_distinct_parent(
        self, backend: LocalStorageBackend, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = owned()
        second = owned()
        probed: list[str] = []
        real_mkstemp = backend_module.tempfile.mkstemp

        def recording_mkstemp(*args: object, **kwargs: object):
            probed.append(str(kwargs.get("dir")))
            return real_mkstemp(*args, **kwargs)

        monkeypatch.setattr(backend_module.tempfile, "mkstemp", recording_mkstemp)

        backend.verify_destructive_access([first.key, second.key])

        # Nested ACLs and read-only submounts can differ beneath one configured
        # root, so each distinct parent is probed rather than the root alone.
        assert len(set(probed)) == len(
            {str(Path(first.key).parent), str(Path(second.key).parent)}
        )

    def test_refuses_a_root_it_cannot_write_to(
        self, backend: LocalStorageBackend, owned, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receipt = owned()

        def read_only(*_args: object, **_kwargs: object):
            raise PermissionError("read-only file system")

        monkeypatch.setattr(backend_module.tempfile, "mkstemp", read_only)

        # Checked before the first delete: a read-only mount must abort the whole
        # purge rather than fail halfway through it.
        with pytest.raises(PermissionError):
            backend.verify_destructive_access([receipt.key])

    def test_falls_back_to_the_unsupported_probe_for_a_remote_key(
        self, backend: LocalStorageBackend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backend, "direct_path", lambda _key: None)

        # A backend with no local path cannot prove delete capability this way,
        # and says so rather than reporting success it has not established.
        with pytest.raises(NotImplementedError):
            backend.verify_destructive_access(["some/remote/key.bin"])
