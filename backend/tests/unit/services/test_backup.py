"""Branch coverage for backup.verify_backup's archive-corruption checks —
unsafe/duplicate members, symlinks, bad manifests, missing/mismatched files,
and version incompatibility — beyond the happy path in test_backup_restore.py."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.core.config import _overlay
from app.services import backup
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


def _verify_direct(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> "backup.BackupVerification":
    """Bypass discovery (_list_local_backups re-reads the manifest to find the
    backup by id) and validate *archive* directly — some corruptions here also
    break discovery, which isn't what these tests are checking."""
    monkeypatch.setattr(backup, "get_backup_archive_path", lambda _id: archive)
    return backup.verify_backup(_id_from(archive))


def _extract(archive: Path) -> tuple[dict[str, bytes], dict]:
    contents: dict[str, bytes] = {}
    manifest: dict = {}
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            data = tar.extractfile(member).read()
            contents[member.name] = data
            if member.name == "manifest.json":
                manifest = json.loads(data.decode("utf-8"))
    return contents, manifest


def _write(
    archive: Path, contents: dict[str, bytes], *, extra_symlink: str | None = None
) -> None:
    with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w:") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra_symlink:
            info = tarfile.TarInfo(name=extra_symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "manifest.json"
            tar.addfile(info)


def _id_from(archive: Path) -> str:
    return archive.name.removesuffix(".tar.gz").rsplit("-", 1)[-1]


def _fresh_archive(env: BackupEnv) -> tuple[Path, dict[str, bytes], dict]:
    seed_model_with_blob(env, name="Verified", content=b"solid verified\n")
    meta = backup.create_backup()
    archive = Path(meta.path)
    contents, manifest = _extract(archive)
    return archive, contents, manifest


class TestUnsafeMemberName:
    def test_verify_backup_flags_unsafe_member_name(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["../escape.txt"] = b"evil"
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


class TestVerifyBackup:
    def test_verify_backup_flags_symlink_member(self, backup_env: BackupEnv) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        _write(archive, contents, extra_symlink="sneaky-link")

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_missing_manifest(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        del contents["manifest.json"]
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "manifest.json"
            for f in result.findings
        )
        assert result.app_compatible is False

    def test_verify_backup_flags_corrupt_manifest_json(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["manifest.json"] = b"{not valid json"
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_non_dict_manifest(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        contents["manifest.json"] = json.dumps(["not", "a", "dict"]).encode("utf-8")
        _write(archive, contents)

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)

    def test_verify_backup_flags_missing_db_file(self, backup_env: BackupEnv) -> None:
        archive, contents, _ = _fresh_archive(backup_env)
        del contents["db.sqlite3"]
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_member_missing" and f["member"] == "db.sqlite3"
            for f in result.findings
        )

    def test_verify_backup_flags_files_entry_not_a_list(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"] = "not-a-list"
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "files"
            for f in result.findings
        )

    def test_verify_backup_flags_malformed_file_entry(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"] = [{"no_arc_key": True}]
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "files"
            for f in result.findings
        )

    def test_verify_backup_flags_file_entry_missing_from_archive(
        self,
        backup_env: BackupEnv,
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["files"].append({"arc": "files/ghost.stl", "size": 5})
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(
            f["code"] == "backup_member_missing" and f["member"] == "files/ghost.stl"
            for f in result.findings
        )

    def test_verify_backup_flags_file_size_mismatch(
        self, backup_env: BackupEnv
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        entry = manifest["files"][0]
        entry["size"] = entry["size"] + 999
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.valid is False
        assert any(f["code"] == "backup_member_size_mismatch" for f in result.findings)

    def test_verify_backup_flags_incompatible_manifest_version(
        self,
        backup_env: BackupEnv,
    ) -> None:
        archive, contents, manifest = _fresh_archive(backup_env)
        manifest["version"] = "999"
        contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
        _write(archive, contents)

        result = backup.verify_backup(_id_from(archive))

        assert result.app_compatible is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "version"
            for f in result.findings
        )

    def test_verify_backup_flags_unreadable_archive(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive, _contents, _manifest = _fresh_archive(backup_env)
        archive.write_bytes(b"not a gzip file at all")

        result = _verify_direct(archive, monkeypatch)

        assert result.valid is False
        assert any(
            f["code"] == "backup_manifest_invalid" and f["member"] == "archive"
            for f in result.findings
        )


class TestRestoreJournalV2:
    def test_reports_no_recovery_when_journal_directory_is_unreadable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        try:
            assert backup.inspect_restore_recovery() is True
            assert backup.restore_in_progress() is True
        finally:
            backup._restore_gate.clear()

    def test_gates_recovery_when_database_swap_is_journaled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
            "backend": "local",
            "namespaces": [],
        }
        swap = {
            "event": "database_swap_intent",
            "backup_id": "swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        (tmp_path / ".restore-swap.journal").write_text(
            json.dumps(started) + "\n" + json.dumps(swap) + "\n"
        )
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        marker_calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            backup,
            "_active_restore_marker",
            lambda *args, **kwargs: marker_calls.append((args, kwargs)) or True,
        )
        backup._restore_gate.clear()

        assert backup.inspect_restore_recovery() is True
        assert backup.restore_in_progress() is True
        assert marker_calls
        backup._restore_gate.clear()

    def test_returns_none_when_journal_discovery_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_when_multiple_journals_are_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-one.journal").write_text("{}")
        (tmp_path / ".restore-two.journal").write_text("{}")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_for_a_journal_with_an_invalid_filename(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "glob", lambda *_args: [Path("invalid")])

        assert backup.unresolved_restore_backup_id() is None

    def test_returns_none_for_a_journal_without_a_backup_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "glob", lambda *_args: [Path(".restore-.journal")])

        assert backup.unresolved_restore_backup_id() is None

    def test_routes_a_readable_journal_to_its_filename_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        journal = tmp_path / ".restore-routed.journal"
        journal.write_text('{"event":"started","backup_id":"tampered"}\n')
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() == "routed"

    def test_rejects_a_non_object_journal_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-list.journal").write_text("[]\n")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_rejects_an_empty_journal_header(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".restore-empty.journal").write_bytes(b"")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)

        assert backup.unresolved_restore_backup_id() is None

    def test_upgrades_a_matching_v1_journal_forward_only(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-abc.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 1,
                    "backup_id": "abc",
                    "archive_sha256": "archive-hash",
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        state = backup._prepare_restore_journal(  # noqa: SLF001
            path,
            backup_id="abc",
            archive_sha256="archive-hash",
            blobs=[],
        )

        assert state.started["version"] == 1
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events[-1]["event"] == "journal_upgrade"
        assert events[-1]["backup_id"] == "abc"
        assert events[-1]["from_version"] == 1
        assert events[-1]["to_version"] == 2
        assert isinstance(events[-1]["operation_nonce"], str)
        assert len(events[-1]["operation_nonce"]) == 64
        assert events[-1]["archive_sha256"] == "archive-hash"

    def test_interrupted_journal_gates_mutations_across_restart(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / ".restore-resume.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "resume",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _overlay["backup_dir"] = tmp_path
        backup._restore_gate.clear()
        try:
            assert backup.inspect_restore_recovery() is True
            assert backup.restore_in_progress() is True
            assert backup.unresolved_restore_backup_id() == "resume"
        finally:
            backup._restore_gate.clear()
            _overlay.pop("backup_dir", None)

    def test_invalid_journal_allows_no_restore_bypass(self, tmp_path: Path) -> None:
        (tmp_path / ".restore-unknown.journal").write_text("not-json\n")
        _overlay["backup_dir"] = tmp_path
        backup._restore_gate.clear()
        try:
            assert backup.inspect_restore_recovery() is True
            # A malformed journal has no resumable identity.  The maintenance
            # gate remains set, so no other backup can bypass the unresolved
            # operation.
            assert backup.unresolved_restore_backup_id() is None
        finally:
            backup._restore_gate.clear()
            _overlay.pop("backup_dir", None)

    def test_no_journal_leaves_restore_maintenance_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        backup._restore_gate.clear()

        assert backup.inspect_restore_recovery() is False
        assert backup.restore_in_progress() is False

    def test_unreadable_journal_directory_keeps_pending_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_glob(_path: Path, _pattern: str):
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "glob", fail_glob)

        assert backup._restore_journal_pending() is True

    def test_rejects_an_unbalanced_mutating_operation(self) -> None:
        backup._active_mutations = 0

        with pytest.raises(RuntimeError, match="unbalanced_mutating_operation"):
            backup.end_mutating_operation()

    def test_drains_a_balanced_mutating_operation(self) -> None:
        backup._restore_gate.clear()
        assert backup.begin_mutating_operation() is True
        backup.end_mutating_operation()

    def test_times_out_when_a_mutation_never_drains(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup._restore_gate.clear()
        backup._active_mutations = 1
        monkeypatch.setattr(backup, "_RESTORE_DRAIN_TIMEOUT_S", 0)
        try:
            with pytest.raises(backup.RestoreConflictError, match="still active"):
                backup._begin_restore_maintenance()
            assert backup.restore_in_progress() is False
        finally:
            backup._active_mutations = 0

    def test_rejects_mutation_while_restore_maintenance_is_active(self) -> None:
        backup._restore_gate.set()
        try:
            assert backup.begin_mutating_operation() is False
        finally:
            backup._restore_gate.clear()


class TestBackupStorageHelpers:
    def test_rejects_a_non_sqlite_database_backup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "db_url", "postgresql://db.example/vault")

        with pytest.raises(
            backup.DatabaseBackupNotSupportedError,
            match="database_backup_not_supported",
        ):
            backup._require_database_backup_support()

    def test_returns_none_when_cloud_backups_are_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", None)

        assert backup._get_backup_s3() is None

    def test_returns_none_when_cloud_client_initialization_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "backup-bucket")
        monkeypatch.setattr(
            "boto3.client", lambda **_kwargs: (_ for _ in ()).throw(OSError("offline"))
        )

        assert backup._get_backup_s3() is None
        assert backup._backup_s3 is False

    def test_rejects_a_missing_sqlite_database_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_db_path", lambda: tmp_path / "missing.sqlite")

        with pytest.raises(FileNotFoundError):
            with backup._sqlite_snapshot_file():
                pass

    def test_rejects_a_snapshot_with_a_failed_integrity_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Connection:
            def __enter__(self) -> "_Connection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, _statement: str) -> "_Connection":
                return self

            def fetchone(self) -> tuple[str]:
                return ("corrupt",)

        monkeypatch.setattr(backup.sqlite3, "connect", lambda _path: _Connection())

        with pytest.raises(RuntimeError, match="integrity_check_failed"):
            backup._validate_sqlite_snapshot(tmp_path / "snapshot.sqlite")

    def test_restores_database_bytes_through_a_temporary_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path)
        target = tmp_path / "vault.sqlite"
        observed: list[Path] = []

        monkeypatch.setattr(backup, "_db_path", lambda: target)

        def capture_snapshot(path: Path) -> None:
            observed.append(path)
            assert path.read_bytes() == b"snapshot-bytes"

        monkeypatch.setattr(backup, "_restore_database_from_path", capture_snapshot)

        backup._restore_database(b"snapshot-bytes")

        assert len(observed) == 1
        assert not observed[0].exists()

    def test_uses_engine_fallback_when_factory_has_no_dispose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Engine:
            disposed = False

            def dispose(self) -> None:
                self.disposed = True

        engine = _Engine()
        monkeypatch.setattr(backup, "get_session_factory", lambda: object())
        monkeypatch.setattr(backup, "get_engine", lambda: engine)

        backup._dispose_session_engine()

        assert engine.disposed is True

    def test_returns_unknown_when_active_marker_read_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_factory():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(backup, "get_session_factory", fail_factory)

        assert backup._active_restore_marker("backup-id") is None

    def test_rejects_a_journal_with_an_unknown_version(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-unknown.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 99,
                    "backup_id": "unknown",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_a_v2_journal_with_an_invalid_nonce(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-invalid-nonce.journal"
        path.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "invalid-nonce",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "z" * 64,
                }
            )
            + "\n"
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_rejects_a_duplicate_database_swap_event(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-duplicate-swap.journal"
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "duplicate-swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        swap = {
            "event": "database_swap_intent",
            "backup_id": "duplicate-swap",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path.write_text("\n".join(json.dumps(event) for event in (started, swap, swap)))

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._load_restore_journal(path)

    def test_accepts_a_terminal_complete_journal_event(self, tmp_path: Path) -> None:
        path = tmp_path / ".restore-complete.journal"
        started = {
            "event": "started",
            "version": 2,
            "backup_id": "complete",
            "archive_sha256": "a" * 64,
            "operation_nonce": "b" * 64,
        }
        path.write_text(
            json.dumps(started) + "\n" + json.dumps({"event": "complete"}) + "\n"
        )

        state = backup._load_restore_journal(path)

        assert state.started["backup_id"] == "complete"

    def test_rejects_a_journal_event_without_a_generation(self) -> None:
        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup._journal_generation({})

    def test_skips_a_backup_with_an_invalid_creation_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = backup.BackupMeta(
            id="invalid-date",
            created_at="not-a-timestamp",
            size_bytes=1,
            storage_backend="local",
            file_count=0,
            app_version="0.13.0",
            path="invalid-date.tar.gz",
        )
        monkeypatch.setattr(backup, "list_backups", lambda: [candidate])

        assert backup.purge_old_backups(retain_days=1) == 0
