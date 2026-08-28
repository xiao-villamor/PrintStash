"""Local storage writes publish atomically and retain ownership evidence.

Create-only publication protects concurrent uploads, while replacement and
rollback preserve bytes when a destination changes during the operation.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Barrier, Thread

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend, StorageCollisionError


class TestReplaceStream:
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
        real_replace = storage_backend.os.replace

        def raced_replace(source, target):
            real_replace(source, target)
            Path(source).write_bytes(b"new-user-file")

        monkeypatch.setattr(storage_backend.os, "replace", raced_replace)

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
