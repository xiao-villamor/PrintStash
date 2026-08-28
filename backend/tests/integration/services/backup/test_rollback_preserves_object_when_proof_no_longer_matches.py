"""Defends rollback preserves object when proof no longer matches at the services backup integration boundary.

A regression could make backup recovery delete or restore bytes without valid proof.
"""

from __future__ import annotations

from ._backup_shared import (
    BackupEnv,
    CreationReceipt,
    MagicMock,
    Path,
    backup,
    pytest,
)


def test_rollback_preserves_object_when_proof_no_longer_matches(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ProofMismatchBackend:
        @staticmethod
        def rollback_create(_receipt: CreationReceipt) -> bool:
            return False

    backend = ProofMismatchBackend()
    monkeypatch.setattr(backup, "get_backend", lambda: backend)
    receipt = CreationReceipt(
        key="vault-data/blob",
        size=1,
        token="proof-mismatch",
        backend="fake",
        namespace="test",
    )
    applied = [backup._AppliedBlob(key=receipt.key, receipt=receipt)]

    backup._rollback_applied_blobs(applied)

    assert "preserved uncertain storage key vault-data/blob" in caplog.text


def test_rollback_continues_after_cleanup_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")

    class FailingCleanupBackend:
        @staticmethod
        def rollback_create(receipt: CreationReceipt) -> bool:
            if receipt.key == str(first_path):
                raise RuntimeError("cleanup failed")
            Path(receipt.key).unlink()
            return True

    backend = FailingCleanupBackend()
    monkeypatch.setattr(backup, "get_backend", lambda: backend)
    first_receipt = CreationReceipt(
        key=str(first_path),
        size=5,
        token="first",
        backend="fake",
        namespace="test",
    )
    second_receipt = CreationReceipt(
        key=str(second_path),
        size=6,
        token="second",
        backend="fake",
        namespace="test",
    )
    applied = [
        backup._AppliedBlob(key=str(second_path), receipt=second_receipt),
        backup._AppliedBlob(key=str(first_path), receipt=first_receipt),
    ]

    backup._rollback_applied_blobs(applied)

    assert first_path.read_bytes() == b"first"
    assert not second_path.exists()
    assert f"restore rollback failed for storage key {first_path}" in caplog.text


def test_apply_staged_blobs_rejects_duplicate_destination(
    backup_env: BackupEnv,
) -> None:
    first = backup_env.root / "first"
    second = backup_env.root / "second"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    destination = str(backup_env.data_dir / "duplicate.stl")
    blobs = [
        backup._StagedBlob(key=destination, path=first),
        backup._StagedBlob(key=destination, path=second),
    ]

    with pytest.raises(
        backup.RestoreConflictError, match="restore_duplicate_destination"
    ):
        backup._apply_staged_blobs(blobs, backup_env.root / "rollback")

    assert not Path(destination).exists()


def test_apply_staged_blobs_rolls_back_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "blob"
    staged.write_bytes(b"five!")
    destination = tmp_path / "published"

    class SizeMismatchBackend:
        @staticmethod
        def exists(_key: str) -> bool:
            return False

        @staticmethod
        def direct_path(_key: str) -> None:
            return None

        @staticmethod
        def create_stream(source, key: str) -> CreationReceipt:
            destination.write_bytes(source.read())
            return CreationReceipt(
                key=key,
                size=1,
                token="token",
                backend="fake",
                namespace="test",
            )

        @staticmethod
        def rollback_create(_receipt: CreationReceipt) -> bool:
            destination.unlink()
            return True

    backend = SizeMismatchBackend()
    monkeypatch.setattr(backup, "get_backend", lambda: backend)

    with pytest.raises(RuntimeError, match="restore_blob_size_mismatch"):
        backup._apply_staged_blobs(
            [backup._StagedBlob(key="vault-data/blob", path=staged)],
            tmp_path / "rollback",
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("/absolute/blob", id="absolute"),
        pytest.param("vault-data/../escape", id="traversal"),
        pytest.param("other-prefix/blob", id="wrong-prefix"),
    ],
)
def test_validate_restore_key_rejects_remote_key_outside_namespace(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class OpaqueBackend:
        @staticmethod
        def direct_path(_key: str) -> None:
            return None

    backend = OpaqueBackend()
    monkeypatch.setattr(backup, "get_backend", lambda: backend)

    with pytest.raises(RuntimeError, match="backup_restore_key_outside_storage"):
        backup._validate_restore_key(key)


def test_validate_restore_key_rejects_local_path_outside_managed_roots(
    backup_env: BackupEnv,
) -> None:
    outside = backup_env.root / "outside" / "blob.stl"

    with pytest.raises(RuntimeError, match="backup_restore_key_outside_storage"):
        backup._validate_restore_key(str(outside))


def test_restore_database_rejects_non_file_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "_db_path", lambda: None)

    with pytest.raises(RuntimeError, match="cannot restore to non-file database"):
        backup._restore_database_from_path(tmp_path / "source.sqlite3")


def test_restore_database_rejects_failed_destination_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.sqlite3"
    destination_path = tmp_path / "destination.sqlite3"
    source_path.write_bytes(b"source")
    destination_path.write_bytes(b"destination")
    source = MagicMock()
    destination = MagicMock()
    source.__enter__.return_value = source
    destination.__enter__.return_value = destination
    destination.execute.return_value.fetchone.return_value = ("corrupt",)
    connections = iter([source, destination])
    monkeypatch.setattr(backup, "_db_path", lambda: destination_path)
    monkeypatch.setattr(backup, "_validate_sqlite_snapshot", lambda _path: None)
    monkeypatch.setattr(backup, "_dispose_session_engine", lambda: None)
    monkeypatch.setattr(
        backup.sqlite3, "connect", lambda *_args, **_kwargs: next(connections)
    )

    with pytest.raises(RuntimeError, match="restored_database_integrity_check_failed"):
        backup._restore_database_from_path(source_path)
