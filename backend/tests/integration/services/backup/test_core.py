"""Backup & restore round-trip tests.

These exercise the local-storage backup path end to end: create an archive
from a populated vault (DB + blobs), then prove a restore brings the database
rows and the stored file bytes back after a simulated disaster.

The shared in-memory test harness can't be used here: ``create_backup`` reads
the SQLite database *as a file* (``_backup_sqlite_copy``) and ``_restore_database``
writes the file back, so these tests stand up a self-contained file-based DB and
a local storage root under ``tmp_path``.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import threading
import time
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

import app.services.backup as backup
import app.services.storage_backend as storage_backend
from app.core.config import _overlay
from app.db.models import (
    SENTINEL_FILE_HASH,
    SENTINEL_MODEL_HASH,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    OwnedStorageObject,
    PrintJobState,
    RestoreMarker,
    StorageObjectState,
)
from app.services.auth import create_access_token
from app.services.storage_backend import get_backend
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.factories import (
    build_cover,
    build_file,
    build_model,
    build_owned_storage_object,
    build_print_job,
    build_printer,
    build_provenance_source,
    build_user,
    store_owned_bytes,
)
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


def _rewrite_backup_archive(
    env: BackupEnv,
    archive: Path,
    mutate_manifest: Callable[[dict], None] | None,
    *,
    raw_manifest: bytes | None = None,
    drop_member: str | None = None,
    duplicate_member: str | None = None,
    directory_member: str | None = None,
    extra_member: tuple[str, bytes] | None = None,
) -> None:
    """Rewrite one owned archive while refreshing its exact ownership proof."""
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(archive, mode="r:gz") as source:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            entries.append((member, data))
    manifest_info, manifest_data = next(
        (member, data)
        for member, data in entries
        if member.name == "manifest.json" and data is not None
    )
    if raw_manifest is None:
        manifest = json.loads(manifest_data)
        assert mutate_manifest is not None
        mutate_manifest(manifest)
        rewritten_manifest = json.dumps(manifest).encode("utf-8")
    else:
        rewritten_manifest = raw_manifest

    output = io.BytesIO()
    duplicate: tuple[tarfile.TarInfo, bytes] | None = None
    with gzip.GzipFile(fileobj=output, mode="wb") as compressed:
        with tarfile.open(fileobj=compressed, mode="w:") as destination:
            for member, data in entries:
                if member.name == drop_member:
                    continue
                if member is manifest_info:
                    info = tarfile.TarInfo("manifest.json")
                    info.size = len(rewritten_manifest)
                    destination.addfile(info, io.BytesIO(rewritten_manifest))
                    if member.name == duplicate_member:
                        duplicate_info = tarfile.TarInfo("manifest.json")
                        duplicate_info.size = len(rewritten_manifest)
                        destination.addfile(
                            duplicate_info, io.BytesIO(rewritten_manifest)
                        )
                    continue
                if member.name == directory_member:
                    info = tarfile.TarInfo(member.name)
                    info.type = tarfile.DIRTYPE
                    destination.addfile(info)
                    continue
                if data is None:
                    destination.addfile(member)
                    continue
                destination.addfile(member, io.BytesIO(data))
                if member.name == duplicate_member:
                    duplicate = (member, data)
            if duplicate is not None:
                destination.addfile(duplicate[0], io.BytesIO(duplicate[1]))
            if extra_member is not None:
                name, data = extra_member
                info = tarfile.TarInfo(name)
                info.size = len(data)
                destination.addfile(info, io.BytesIO(data))

    from app.services.storage_ownership import replace_owned_bytes

    with env.new_session() as session:
        replace_owned_bytes(
            session,
            storage_backend.LocalStorageBackend(),
            str(archive),
            output.getvalue(),
            object_kind="backup",
        )
        session.commit()


def _archive_manifest(archive: Path) -> dict:
    with tarfile.open(archive, mode="r:gz") as source:
        stream = source.extractfile("manifest.json")
        assert stream is not None
        manifest = json.loads(stream.read())
    assert isinstance(manifest, dict)
    return manifest


def _leave_manifest_unchanged(_manifest: dict) -> None:
    pass


def _set_unsupported_version(manifest: dict) -> None:
    manifest["version"] = "999"


def _remove_files_evidence(manifest: dict) -> None:
    manifest.pop("files")


def _replace_entry_with_invalid_shape(manifest: dict) -> None:
    manifest["files"][0] = "invalid"


def _remove_entry_sha256(manifest: dict) -> None:
    manifest["files"][0].pop("sha256")


def _remove_entry_provider(manifest: dict) -> None:
    manifest["files"][0].pop("provider")


def _duplicate_manifest_entry(manifest: dict) -> None:
    manifest["files"].append(dict(manifest["files"][0]))
    manifest["file_count"] += 1


def _increment_manifest_file_count(manifest: dict) -> None:
    manifest["file_count"] += 1


def _increment_manifest_entry_size(manifest: dict) -> None:
    manifest["files"][0]["size"] += 1


def _replace_entry_sha256(manifest: dict) -> None:
    manifest["files"][0]["sha256"] = "0" * 64


def _rewrite_manifest_with(
    env: BackupEnv,
    archive: Path,
    _member: str,
    *,
    mutation: Callable[[dict], None],
) -> None:
    _rewrite_backup_archive(env, archive, mutation)


def _rewrite_raw_manifest(
    env: BackupEnv, archive: Path, _member: str, *, payload: bytes
) -> None:
    _rewrite_backup_archive(env, archive, None, raw_manifest=payload)


def _drop_named_member(
    env: BackupEnv, archive: Path, _member: str, *, name: str
) -> None:
    _rewrite_backup_archive(env, archive, _leave_manifest_unchanged, drop_member=name)


def _duplicate_named_member(
    env: BackupEnv, archive: Path, _member: str, *, name: str
) -> None:
    _rewrite_backup_archive(
        env, archive, _leave_manifest_unchanged, duplicate_member=name
    )


def _drop_manifest_blob(env: BackupEnv, archive: Path, member: str) -> None:
    _rewrite_backup_archive(env, archive, _leave_manifest_unchanged, drop_member=member)


def _duplicate_manifest_blob(env: BackupEnv, archive: Path, member: str) -> None:
    _rewrite_backup_archive(
        env, archive, _leave_manifest_unchanged, duplicate_member=member
    )


def _replace_manifest_blob_with_directory(
    env: BackupEnv, archive: Path, member: str
) -> None:
    _rewrite_backup_archive(
        env, archive, _leave_manifest_unchanged, directory_member=member
    )


def _add_named_member(
    env: BackupEnv,
    archive: Path,
    _member: str,
    *,
    name: str,
    content: bytes,
) -> None:
    _rewrite_backup_archive(
        env,
        archive,
        _leave_manifest_unchanged,
        extra_member=(name, content),
    )


def _manifest_member_name(_member: str) -> str:
    return "manifest.json"


def _blob_member_name(member: str) -> str:
    return member


def _write_journal_events(path: Path, events: list[object]) -> None:
    path.write_text("".join(f"{json.dumps(event)}\n" for event in events))


def _append_malformed_journal(
    path: Path, _events: list[object], _intent: dict, _key: str
) -> None:
    path.write_bytes(path.read_bytes() + b"{\n")


def _append_non_object_event(
    path: Path, events: list[object], _intent: dict, _key: str
) -> None:
    events.append([])
    _write_journal_events(path, events)


def _replace_first_journal_event(
    path: Path, events: list[object], _intent: dict, _key: str
) -> None:
    events[0]["event"] = "intent"
    _write_journal_events(path, events)


def _append_unknown_journal_event(
    path: Path, events: list[object], _intent: dict, key: str
) -> None:
    events.append({"event": "unknown", "key": key, "generation": 1})
    _write_journal_events(path, events)


def _replace_generation_with_text(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    intent["generation"] = "one"
    _write_journal_events(path, events)


def _skip_journal_generation(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    intent["generation"] = 2
    _write_journal_events(path, events)


def _append_duplicate_intent(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    events.append(dict(intent))
    _write_journal_events(path, events)


def _publish_before_intent(
    path: Path, events: list[object], _intent: dict, key: str
) -> None:
    events[1] = {"event": "published", "key": key, "generation": 1}
    _write_journal_events(path, events)


def _publish_wrong_generation(
    path: Path, events: list[object], _intent: dict, key: str
) -> None:
    events.append({"event": "published", "key": key, "generation": 2})
    _write_journal_events(path, events)


def _append_duplicate_publication(
    path: Path, events: list[object], _intent: dict, key: str
) -> None:
    published = {"event": "published", "key": key, "generation": 1}
    events.extend([published, dict(published)])
    _write_journal_events(path, events)


def _replace_started_field(
    path: Path,
    events: list[object],
    _intent: dict,
    _key: str,
    *,
    field: str,
    value: object,
) -> None:
    events[0][field] = value
    _write_journal_events(path, events)


def _replace_intent_key(
    path: Path, events: list[object], intent: dict, key: str
) -> None:
    intent["key"] = str(Path(key).parent / "unlisted.bin")
    _write_journal_events(path, events)


def _increment_intent_size(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    intent["size"] += 1
    _write_journal_events(path, events)


def _replace_intent_hash(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    intent["sha256"] = "0" * 64
    _write_journal_events(path, events)


def _replace_intent_namespace(
    path: Path, events: list[object], intent: dict, _key: str
) -> None:
    intent["namespace"] = "foreign:/vault"
    _write_journal_events(path, events)


def _invalidate_receipt_token(published: dict) -> None:
    published["token"] = None


def _increment_receipt_size(published: dict) -> None:
    published["size"] += 1


def _return_invalid_size_receipt(
    backend: storage_backend.StorageBackend,
    _real_create: Callable,
    content: bytes,
    _source,
    destination: str,
):
    return storage_backend.CreationReceipt(
        key=destination,
        size=len(content) + 1,
        token="invalid-size",
        backend=backend.backend_name,
        namespace=backend.namespace_for(destination),
    )


def _publish_wrong_hash(
    _backend: storage_backend.StorageBackend,
    real_create: Callable,
    content: bytes,
    _source,
    destination: str,
):
    return real_create(io.BytesIO(b"x" * len(content)), destination)


def _publish_vanished_object(
    _backend: storage_backend.StorageBackend,
    real_create: Callable,
    content: bytes,
    _source,
    destination: str,
):
    receipt = real_create(io.BytesIO(b"x" * len(content)), destination)
    Path(destination).unlink()
    return receipt


def _seed_document_with_blob(env: BackupEnv, *, name: str, content: bytes) -> str:
    """Create a binary Document row and write its blob. Returns the storage key."""
    with env.new_session() as session:
        doc = Document(name=name, kind=DocumentKind.PDF)
        session.add(doc)
        session.commit()
        session.refresh(doc)
        key = get_backend().document_file_key(doc.id, name)
        get_backend().write_bytes(content, key)
        doc.filename = name
        doc.size_bytes = len(content)
        session.add(doc)
        session.commit()
        return key


def _read_model_names(env: BackupEnv) -> list[str]:
    """Read model names through a brand-new engine so the restored DB file is
    seen, not a connection cached against the pre-restore file."""
    eng = create_engine(
        f"sqlite:///{env.db_file}", connect_args={"check_same_thread": False}
    )
    try:
        with Session(eng) as session:
            return [m.name for m in session.exec(select(Model)).all()]
    finally:
        eng.dispose()


def _auth_headers(env: BackupEnv) -> dict[str, str]:
    with env.new_session() as session:
        user = build_user(
            session,
            username="backup-admin",
            password="Password123",
            active=True,
            superuser=True,
        )
        token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Restore round trip
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Audit trail (0.8.5 item 3): backup/restore mutate the filesystem and swap
# the DB file, so they don't flow through the ORM after_flush hook — the
# service writes AuditLog rows explicitly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Restore gate (item 11: quiesce background loops during restore)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Corrupted / invalid archives (local — no S3 endpoint needed)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Narrow branch closures: S3 key helper, size-mismatch abort, list_backups
# merge/dedup, and the "member present but unreadable" edge cases in
# _read_manifest / _restore_key_map / restore_backup (all local-only, no
# S3 endpoint needed).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S3 backup destination (independent from vault storage) — needs a real endpoint
# ---------------------------------------------------------------------------

# The `s3` marker tells conftest.py these need a real endpoint; it starts one
# container per run and stops the session if Docker is not available.
requires_s3 = pytest.mark.s3


@pytest.fixture
def backup_s3_env(backup_env: BackupEnv) -> Iterator[BackupEnv]:
    import uuid as _uuid

    bucket = f"printstash-backup-test-{_uuid.uuid4().hex[:12]}"
    _overlay.update(
        {
            "backup_s3_bucket": bucket,
            "backup_s3_endpoint_url": s3_endpoint(),
            "backup_s3_region": "us-east-1",
            "backup_s3_access_key": S3_ACCESS_KEY,
            "backup_s3_secret_key": S3_SECRET_KEY,
        }
    )
    # First call to _get_backup_s3() lazily creates the bucket via boto3's
    # default behavior is *not* create-on-connect, so create it explicitly.
    s3 = backup._get_backup_s3()
    s3.create_bucket(Bucket=bucket)
    try:
        yield backup_env
    finally:
        for key in (
            s3.get_paginator("list_objects_v2")
            .paginate(Bucket=bucket)
            .search("Contents[].Key")
        ):
            if key:
                s3.delete_object(Bucket=bucket, Key=key)
        s3.delete_bucket(Bucket=bucket)
        for field in (
            "backup_s3_bucket",
            "backup_s3_endpoint_url",
            "backup_s3_region",
            "backup_s3_access_key",
            "backup_s3_secret_key",
        ):
            _overlay.pop(field, None)


# The router's own branches (404/409/500/501, auth) moved to
# tests/integration/api/v1/test_backup.py, the mirror of app/api/v1/backup.py.


class TestBackupS3Key:
    def test_backup_s3_key_prefixes_archive_name(self):
        assert (
            backup._backup_s3_key("printstash-backup-20240101-000000-abc123.tar.gz")
            == "printstash-backups/printstash-backup-20240101-000000-abc123.tar.gz"
        )


class TestBackupSqliteCopy:
    def test_backup_sqlite_copy_contains_committed_model(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Snapshot", content=b"snapshot")

        snapshot = backup._backup_sqlite_copy()

        assert b"Snapshot" in snapshot

    def test_backup_sqlite_copy_raises_for_non_file_db(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(backup, "_db_path", lambda: None)
        with pytest.raises(RuntimeError, match="not a file-based SQLite database"):
            backup._backup_sqlite_copy()


class TestFindBlobs:
    def test_find_blobs_fails_for_unreadable_owned_blob(self, backup_env: BackupEnv):
        _model_id, key = seed_model_with_blob(
            backup_env, name="Widget", content=b"solid widget\n"
        )
        Path(key).unlink()

        with pytest.raises(FileNotFoundError):
            backup._find_blobs()


class TestCreateBackup:
    def test_create_backup_archive_contents(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
        seed_model_with_blob(backup_env, name="Gadget", content=b"solid gadget\n")

        meta = backup.create_backup()

        assert meta.file_count == 2
        assert meta.location == "local"
        archive = Path(meta.path)
        assert archive.exists() and archive.stat().st_size > 0
        assert meta.size_bytes == archive.stat().st_size

        import gzip
        import tarfile

        names = set()
        with gzip.open(archive, "rb") as gz:
            with tarfile.open(fileobj=gz, mode="r|") as tar:
                for member in tar:
                    names.add(member.name)

        assert "db.sqlite3" in names
        assert "manifest.json" in names
        # Two blobs captured under files/.
        assert sum(1 for n in names if n.startswith("files/") and n != "files/") == 2

    def test_backs_up_a_model_whose_superseded_thumbnail_is_absent(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
        backend = get_backend()
        with backup_env.new_session() as session:
            file_row = session.exec(select(File)).one()
            assert file_row.id is not None
            current_thumbnail = backend.thumbnail_variant_key(
                file_row.id, file_row.sha256 or "a" * 64, "b" * 64
            )
            backend.write_bytes(b"current thumbnail", current_thumbnail)
            file_row.thumbnail_path = current_thumbnail
            session.add(file_row)
            session.commit()

        meta = backup.create_backup()

        assert meta.file_count == 2

    def test_create_backup_includes_rows_committed_only_to_wal(
        self, backup_env: BackupEnv
    ):
        """A raw copy of the main SQLite file omits committed WAL pages."""
        with backup_env.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

        seed_model_with_blob(
            backup_env,
            name="Committed In WAL",
            content=b"solid committed-in-wal\n",
        )
        wal_path = Path(f"{backup_env.db_file}-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0

        meta = backup.create_backup()
        snapshot_path = backup_env.root / "snapshot.sqlite3"
        with tarfile.open(meta.path, mode="r:gz") as archive:
            db_member = archive.extractfile("db.sqlite3")
            assert db_member is not None
            snapshot_path.write_bytes(db_member.read())

        with sqlite3.connect(snapshot_path) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM models")]
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert "Committed In WAL" in names

    def test_create_backup_fails_when_owned_blob_is_missing(
        self, backup_env: BackupEnv
    ):
        _, key = seed_model_with_blob(backup_env, name="Missing", content=b"gone")
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink()  # Simulate loss outside PrintStash; unchecked delete is disabled.

        with pytest.raises(FileNotFoundError):
            backup.create_backup()

    def test_create_backup_fails_if_blob_vanishes_mid_write(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """A backup never reports success after a censused blob vanishes."""
        seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")

        def _boom(*args, **kwargs):
            raise OSError("vanished")

        monkeypatch.setattr(backup, "_add_file_to_tar", _boom)

        with caplog.at_level("ERROR"), pytest.raises(OSError, match="vanished"):
            backup.create_backup()

        assert any(
            "failed while streaming owned blobs" in r.message for r in caplog.records
        )
        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []

    def test_create_backup_raises_when_blob_size_changes_mid_archive(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """If the recorded size for a censused blob no longer matches what actually
        gets streamed into the tar, the archive must not be reported as complete."""
        _, key = seed_model_with_blob(
            backup_env, name="Widget", content=b"solid widget\n"
        )

        real_find_blobs = backup._find_blobs

        def _wrong_size(session: Session | None = None):
            return [(k, size + 999) for k, size in real_find_blobs(session)]

        monkeypatch.setattr(backup, "_find_blobs", _wrong_size)

        with (
            caplog.at_level("ERROR"),
            pytest.raises(RuntimeError, match="backup_blob_size_changed"),
        ):
            backup.create_backup()

        assert any(
            "failed while streaming owned blobs" in r.message for r in caplog.records
        )
        # The partial archive must not be left behind as if it were valid.
        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []

    def test_create_backup_rejects_same_size_content_change_during_streaming(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = b"same-size-old"
        replacement = b"same-size-new"
        _, key = seed_model_with_blob(
            backup_env, name="Changing Content", content=original
        )
        real_add = backup._add_file_to_tar

        def replace_before_stream(
            archive: tarfile.TarFile, source_key: str, member: str
        ) -> int:
            Path(source_key).write_bytes(replacement)
            return real_add(archive, source_key, member)

        monkeypatch.setattr(backup, "_add_file_to_tar", replace_before_stream)

        with pytest.raises(RuntimeError, match="backup_blob_hash_changed"):
            backup.create_backup()

        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []
        with backup_env.new_session() as session:
            backup_rows = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup"
                )
            ).all()
        assert backup_rows == []

    @requires_s3
    def test_create_backup_uploads_to_s3(self, backup_s3_env: BackupEnv):
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")

        meta = backup.create_backup()

        s3 = backup._get_backup_s3()
        key = backup._backup_s3_key(Path(meta.path).name)
        head = s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
        assert head["ContentLength"] == meta.size_bytes

    def test_backup_excludes_user_owned_external_artifacts(self, backup_env: BackupEnv):
        external = backup_env.root / "nas" / "linked.stl"
        external.parent.mkdir()
        external.write_bytes(b"user-owned")
        with backup_env.new_session() as session:
            model = build_model(session, name="Linked", slug="linked", hash="c" * 64)
            build_file(
                session,
                model,
                path=str(external),
                filename=external.name,
                file_type=FileType.STL,
                external=True,
                size_bytes=external.stat().st_size,
                sha256="d" * 64,
            )

        meta = backup.create_backup()

        assert meta.file_count == 0

    def test_backup_ignores_external_job_sentinel_but_keeps_vault_artifact(
        self,
        backup_env: BackupEnv,
    ):
        """The /dev/null placeholder is not a blob, while real vault files remain."""
        _model_id, vault_key = seed_model_with_blob(
            backup_env, name="Vault artifact", content=b"real vault bytes"
        )
        with backup_env.new_session() as session:
            sentinel_model = build_model(
                session,
                name="__external__",
                slug="__external__",
                hash=SENTINEL_MODEL_HASH,
            )
            sentinel_file = build_file(
                session,
                sentinel_model,
                path="/dev/null",
                filename="__external__",
                file_type=FileType.GCODE,
                version=1,
                size_bytes=0,
                sha256=SENTINEL_FILE_HASH,
            )
            printer = build_printer(session, name="External history")
            session.refresh(sentinel_file)
            session.refresh(printer)
            build_print_job(
                session,
                sentinel_file,
                printer=printer,
                remote_filename="external.gcode",
                source="external",
                state=PrintJobState.COMPLETED,
            )

        meta = backup.create_backup()

        assert meta.file_count == 1
        with tarfile.open(meta.path, mode="r:gz") as archive:
            manifest = archive.extractfile("manifest.json")
            assert manifest is not None
            entries = json.loads(manifest.read())["files"]
        assert [entry["key"] for entry in entries] == [vault_key]

    def test_backup_writes_audit_row(self, backup_env: BackupEnv):
        from app.db.models import AuditLog

        backup.create_backup()

        with backup_env.new_session() as session:
            rows = session.exec(
                select(AuditLog).where(AuditLog.action == "backup.create")
            ).all()
        assert len(rows) == 1
        assert rows[0].resource_type == "backup"

    def test_backup_id_round_trips_despite_timestamped_name(
        self, backup_env: BackupEnv
    ):
        """The archive name embeds a hyphenated timestamp before the id; the id
        derived on list/get must still equal the one create_backup returned
        (regression for the rsplit-based id extraction)."""
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        # id is the trailing 12-hex token, not a timestamp fragment.
        assert len(meta.id) == 12
        assert all(c in "0123456789abcdef" for c in meta.id)
        assert f"-{meta.id}.tar.gz" in Path(meta.path).name

        fetched = backup.get_backup(meta.id)
        assert fetched is not None and fetched.id == meta.id

    def test_create_backup_records_v3_provider_content_evidence(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Evidence", content=b"content-evidence"
        )

        meta = backup.create_backup()

        with tarfile.open(meta.path, mode="r:gz") as archive:
            stream = archive.extractfile("manifest.json")
            assert stream is not None
            manifest = json.loads(stream.read())
        assert manifest["version"] == "3"
        assert manifest["provider_id"] == "local"
        assert manifest["transport"] == "local"
        assert manifest["namespace"] == get_backend().namespace_for(key)
        assert manifest["files"] == [
            {
                "member": manifest["files"][0]["member"],
                "arc": manifest["files"][0]["member"],
                "key": key,
                "provider": "local",
                "provider_id": "local",
                "transport": "local",
                "namespace": get_backend().namespace_for(key),
                "size": len(b"content-evidence"),
                "sha256": "5cd0da5638341f7227386d0fab6742f51f6f740a7971e20e2be0bbdb1a4386f5",
            }
        ]

    def test_create_backup_deduplicates_shared_owned_storage_keys(
        self, backup_env: BackupEnv
    ) -> None:
        content = b"shared-primary-cover"
        with backup_env.new_session() as session:
            model = build_model(session, name="Shared")
            key = str(backup_env.data_dir / "shared.bin")
            Path(key).write_bytes(content)
            build_file(
                session,
                model,
                path=key,
                filename="shared.bin",
                file_type=FileType.STL,
                size_bytes=len(content),
            )
            source = build_provenance_source(session, model)
            build_cover(session, source, storage_key=key, size_bytes=len(content))

        meta = backup.create_backup()

        manifest = _archive_manifest(Path(meta.path))
        assert meta.file_count == 1
        assert [entry["key"] for entry in manifest["files"]] == [key]

    def test_create_backup_rejects_a_missing_source_cover(
        self, backup_env: BackupEnv
    ) -> None:
        with backup_env.new_session() as session:
            model = build_model(session, name="Missing Cover")
            source = build_provenance_source(session, model)
            key = get_backend().source_cover_key(source.id)
            build_cover(session, source, storage_key=key, size_bytes=12)

        with pytest.raises(FileNotFoundError):
            backup.create_backup()

        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []

    def test_creates_a_local_archive_before_uploading_its_cloud_copy(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _BackupObjectStore(b"", "unused")
        store.local_archive_dir = backup_env.backup_dir
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        seed_model_with_blob(backup_env, name="Cloud upload", content=b"upload")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        meta = backup.create_backup()

        assert store.uploaded == Path(meta.path).read_bytes()
        assert store.upload_observations == [
            (f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}", True)
        ]
        with backup_env.new_session() as session:
            rows = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.backend == "backup-s3",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].state is StorageObjectState.COMMITTED

    def test_keeps_the_local_archive_when_cloud_upload_fails(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _BackupObjectStore(b"", "unused", fail_put=OSError("offline"))
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        seed_model_with_blob(backup_env, name="Cloud failure", content=b"upload")
        meta = backup.create_backup()

        assert Path(meta.path).is_file()
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.backend == "backup-s3",
                )
            ).one()
            assert row.state is StorageObjectState.PENDING


class TestValidateCreatedArchivePayload:
    @pytest.mark.parametrize(
        ("mutation", "error_type", "error"),
        [
            pytest.param(
                partial(_drop_named_member, name="manifest.json"),
                RuntimeError,
                "backup_manifest_invalid",
                id="missing-manifest",
            ),
            pytest.param(
                partial(_duplicate_named_member, name="manifest.json"),
                RuntimeError,
                "backup_manifest_invalid",
                id="duplicate-manifest",
            ),
            pytest.param(
                partial(_rewrite_raw_manifest, payload=b"{"),
                json.JSONDecodeError,
                None,
                id="malformed-manifest",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_set_unsupported_version),
                RuntimeError,
                "backup_manifest_invalid",
                id="unsupported-version",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_remove_files_evidence),
                RuntimeError,
                "backup_manifest_invalid",
                id="missing-files-evidence",
            ),
            pytest.param(
                partial(
                    _rewrite_manifest_with,
                    mutation=_replace_entry_with_invalid_shape,
                ),
                RuntimeError,
                "backup_manifest_invalid",
                id="malformed-entry",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_remove_entry_sha256),
                RuntimeError,
                "backup_manifest_invalid",
                id="missing-entry-evidence",
            ),
            pytest.param(
                _drop_manifest_blob,
                RuntimeError,
                "backup_manifest_invalid",
                id="missing-member",
            ),
            pytest.param(
                _duplicate_manifest_blob,
                RuntimeError,
                "backup_manifest_invalid",
                id="duplicate-member",
            ),
            pytest.param(
                _replace_manifest_blob_with_directory,
                RuntimeError,
                "backup_manifest_invalid",
                id="directory-member",
            ),
            pytest.param(
                partial(
                    _rewrite_manifest_with, mutation=_increment_manifest_entry_size
                ),
                RuntimeError,
                "backup_blob_size_changed",
                id="size-mismatch",
            ),
        ],
    )
    def test_completed_archive_rejects_invalid_payload(
        self,
        backup_env: BackupEnv,
        mutation: Callable[[BackupEnv, Path, str], None],
        error_type: type[Exception],
        error: str | None,
    ) -> None:
        seed_model_with_blob(
            backup_env, name="Completed Invalid", content=b"completed-payload"
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        member = _archive_manifest(archive)["files"][0]["member"]

        mutation(backup_env, archive, member)
        before = archive.read_bytes()

        with pytest.raises(error_type, match=error):
            backup._validate_created_archive_payload(archive)

        assert archive.read_bytes() == before


class TestListLocalBackups:
    def test_list_local_backups_empty_when_dir_missing(self, backup_env: BackupEnv):
        import shutil

        shutil.rmtree(backup_env.backup_dir)
        assert backup._list_local_backups() == []
        assert backup.list_backups() == []

    def test_legacy_archive_requires_explicit_adoption(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Legacy adoption", content=b"legacy")
        meta = backup.create_backup()
        archive = Path(meta.path)
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(archive),
                    OwnedStorageObject.object_kind == "backup",
                )
            ).one()
            session.delete(row)
            session.commit()

        assert backup.list_backups() == []
        adopted = backup.adopt_local_backup(archive.name)
        assert adopted.id == meta.id
        assert {item.id for item in backup.list_backups()} == {meta.id}

    def test_legacy_null_receipt_is_unusable_until_full_adoption(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Legacy null receipt", content=b"legacy")
        meta = backup.create_backup()
        archive = Path(meta.path)
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(archive),
                    OwnedStorageObject.object_kind == "backup",
                )
            ).one()
            row.provider_ref = None
            session.add(row)
            session.commit()

        assert backup.list_backups() == []
        assert backup.delete_backup(meta.id) is False
        with pytest.raises(FileNotFoundError):
            backup.restore_backup(meta.id)
        assert archive.exists()

        adopted = backup.adopt_local_backup(archive.name)
        assert adopted.id == meta.id
        assert backup.get_backup(meta.id) is not None
        assert backup.delete_backup(meta.id) is True
        assert not archive.exists()

    def test_archive_adoption_rejects_tampered_bytes(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Tampered adoption", content=b"bytes")
        meta = backup.create_backup()
        archive = Path(meta.path)
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == str(archive))
            ).one()
            session.delete(row)
            session.commit()
        archive.write_bytes(b"not-an-archive")

        with pytest.raises((RuntimeError, gzip.BadGzipFile, tarfile.TarError)):
            backup.adopt_local_backup(archive.name)

    def test_archive_adoption_rejects_an_undeclared_regular_member(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(
            backup_env, name="Undeclared adoption member", content=b"bytes"
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        _rewrite_backup_archive(
            backup_env,
            archive,
            _leave_manifest_unchanged,
            extra_member=("files/undeclared.bin", b"unlisted"),
        )
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(archive),
                    OwnedStorageObject.object_kind == "backup",
                )
            ).one()
            session.delete(row)
            session.commit()

        with pytest.raises(RuntimeError, match="backup_manifest_invalid"):
            backup.adopt_local_backup(archive.name)

        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(archive),
                    )
                ).all()
                == []
            )

    def test_cloud_cache_is_not_a_listable_backup_source(
        self, backup_env: BackupEnv
    ) -> None:
        cache = backup_env.backup_dir / ".cloud-cache" / "source-ref-archive.tar.gz"
        cache.parent.mkdir(parents=True)
        payload = b"disposable cloud cache"
        cache.write_bytes(payload)
        backend = storage_backend.LocalStorageBackend()
        with backup_env.new_session() as session:
            build_owned_storage_object(
                session,
                backend="local",
                namespace=backend.namespace_for(str(cache)),
                key=str(cache),
                object_kind="backup-cloud-cache",
                state=StorageObjectState.COMMITTED,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )

        assert backup.list_backups() == []
        assert backup.list_backup_sources() == []
        assert cache.exists()

    def test_unowned_discovery_returns_only_valid_adoption_candidates(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Legacy discover", content=b"bytes")
        meta = backup.create_backup()
        archive = Path(meta.path)
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(archive),
                    OwnedStorageObject.object_kind == "backup",
                )
            ).one()
            session.delete(row)
            session.commit()
        (
            backup_env.backup_dir / "printstash-backup-invalid-deadbeefcafe.tar.gz"
        ).write_bytes(b"invalid")

        candidates = backup.discover_unowned_local_backups()

        assert [candidate["filename"] for candidate in candidates] == [archive.name]


class TestUploadBackupArchive:
    @pytest.mark.parametrize(
        "filename",
        [
            "../printstash-backup-escaped.tar.gz",
            "printstash-backup-bad\\name.tar.gz",
            "ordinary-archive.tar.gz",
        ],
    )
    def test_rejects_an_unsafe_or_unrecognized_filename(
        self, backup_env: BackupEnv, filename: str
    ) -> None:
        with pytest.raises(ValueError, match="backup_filename_invalid"):
            backup.upload_backup_archive(filename, io.BytesIO(b"unused"))

        assert list(backup_env.backup_dir.iterdir()) == []

    def test_registers_a_valid_uploaded_archive(self, backup_env: BackupEnv) -> None:
        original = backup.create_backup()
        archive = Path(original.path)
        payload = archive.read_bytes()
        filename = archive.name
        assert backup.delete_backup(original.id, source_ref=original.source_ref)

        uploaded = backup.upload_backup_archive(filename, io.BytesIO(payload))

        assert Path(uploaded.path).read_bytes() == payload
        assert (
            backup.get_backup(uploaded.id, source_ref=uploaded.source_ref) is not None
        )

    def test_refuses_to_replace_an_existing_archive(
        self, backup_env: BackupEnv
    ) -> None:
        original = backup.create_backup()

        with pytest.raises(FileExistsError, match="backup_already_exists"):
            backup.upload_backup_archive(
                Path(original.path).name, io.BytesIO(b"unused")
            )

    def test_removes_staging_bytes_when_validation_fails(
        self, backup_env: BackupEnv
    ) -> None:
        filename = "printstash-backup-20260101-000000-invalid.tar.gz"

        with pytest.raises((RuntimeError, gzip.BadGzipFile, tarfile.TarError)):
            backup.upload_backup_archive(filename, io.BytesIO(b"not an archive"))

        assert list(backup_env.backup_dir.iterdir()) == []


class TestListBackups:
    def test_list_backups_skips_archive_with_unreadable_manifest(
        self, backup_env: BackupEnv, caplog: pytest.LogCaptureFixture
    ):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        good = backup.create_backup()

        corrupt_path = (
            backup_env.backup_dir
            / "printstash-backup-20200101-000000-deadbeefcafe.tar.gz"
        )
        corrupt_path.write_bytes(b"not a gzip file at all")

        listed = {m.id for m in backup.list_backups()}
        assert listed == {good.id}  # the corrupt archive is skipped, not raised

    def test_list_backups_prefers_the_local_row_on_an_id_collision(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        local_meta = backup.create_backup()

        s3_only = backup.BackupMeta(
            id="s3-only-id",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=42,
            storage_backend="s3",
            file_count=1,
            app_version="0.0.0",
            path="printstash-backups/s3-only.tar.gz",
            location="s3",
        )
        # Same content identity as the local backup: local has precedence.
        s3_dupe = backup.BackupMeta(
            id=local_meta.id,
            created_at=local_meta.created_at,
            size_bytes=999999,
            storage_backend="s3",
            file_count=999,
            app_version="0.0.0",
            path="printstash-backups/dupe.tar.gz",
            location="s3",
            archive_sha256=local_meta.archive_sha256,
        )
        monkeypatch.setattr(backup, "_list_s3_backups", lambda: [s3_only, s3_dupe])

        merged = {m.id: m for m in backup.list_backups()}

        assert set(merged) == {local_meta.id, "s3-only-id"}
        assert merged[local_meta.id].location == "local"
        assert merged[local_meta.id].file_count == local_meta.file_count

    def test_list_backups_includes_a_backup_that_exists_only_in_the_cloud(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        """Exercises the merge/dedup loop in list_backups() without a real S3
        endpoint: _list_s3_backups() is stubbed to return a cloud-only entry plus
        a duplicate of a local id, and the loop's own logic is what's checked."""
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        local_meta = backup.create_backup()

        cloud_only = backup.BackupMeta(
            id="cloud-only-id",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=123,
            storage_backend="local",
            file_count=1,
            app_version="0.0.0",
            path="cloud-only.tar.gz",
            location="s3",
        )
        duplicate_of_local = backup.BackupMeta(
            id=local_meta.id,
            created_at="1999-01-01T00:00:00+00:00",
            size_bytes=999,
            storage_backend="local",
            file_count=999,
            app_version="stale",
            path="dup.tar.gz",
            location="s3",
            archive_sha256=local_meta.archive_sha256,
        )
        monkeypatch.setattr(
            backup, "_list_s3_backups", lambda: [cloud_only, duplicate_of_local]
        )

        merged = backup.list_backups()

        assert {m.id for m in merged} == {local_meta.id, "cloud-only-id"}
        # Local wins the dup: the merged entry keeps the real local metadata, not
        # the stale S3 stub sharing its id.
        winner = next(m for m in merged if m.id == local_meta.id)
        assert winner.location == "local"
        assert winner.file_count == local_meta.file_count

    @requires_s3
    def test_list_backups_finds_s3_only_backup(self, backup_s3_env: BackupEnv):
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup.create_backup()

        # Simulate cloud-only: the local copy is gone, only the S3 upload remains.
        Path(meta.path).unlink()

        found = backup.get_backup(meta.id)
        assert found is not None
        assert found.location == "s3"
        assert found.file_count == meta.file_count

    def test_a_new_backup_appears_in_the_listing(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        assert any(m.id == meta.id for m in backup.list_backups())

    def test_a_new_backup_is_retrievable_by_id(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        fetched = backup.get_backup(meta.id)

        assert fetched is not None
        assert fetched.id == meta.id
        assert fetched.file_count == 1


class TestReadManifest:
    def test_read_manifest_returns_none_when_manifest_entry_is_a_directory(
        self, tmp_path: Path
    ):
        import tarfile

        archive = tmp_path / "dir-manifest.tar"
        with tarfile.open(archive, mode="w") as tar:
            # A directory-type TarInfo named manifest.json: extractfile() returns
            # None for it, distinct from the "no manifest.json at all" case.
            info = tarfile.TarInfo(name="manifest.json")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)

        # _read_manifest opens the path itself (gzip + streaming read), so wrap
        # a gzip copy of the plain tar above for it to consume.
        import gzip
        import shutil

        gz_archive = tmp_path / "dir-manifest.tar.gz"
        with open(archive, "rb") as src, gzip.open(gz_archive, "wb") as dst:
            shutil.copyfileobj(src, dst)

        assert backup._read_manifest(gz_archive) is None

    def test_read_manifest_returns_none_when_no_manifest_member_present(
        self, tmp_path: Path
    ):
        import gzip
        import io
        import tarfile

        gz_archive = tmp_path / "no-manifest.tar.gz"
        with (
            gzip.open(gz_archive, "wb") as gz,
            tarfile.open(fileobj=gz, mode="w") as tar,
        ):
            data = b"fake db bytes"
            info = tarfile.TarInfo(name="db.sqlite3")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        assert backup._read_manifest(gz_archive) is None

    def test_read_manifest_returns_none_when_manifest_member_unreadable(
        self, tmp_path: Path
    ):
        """extractfile() returns None for non-regular members (e.g. a directory
        entry) even when the name matches "manifest.json" exactly."""
        import gzip
        import tarfile

        archive = tmp_path / "printstash-backup-20200101-000000-badbadbadbad.tar.gz"
        with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            info = tarfile.TarInfo(name="manifest.json")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)

        assert backup._read_manifest(archive) is None

    def test_read_manifest_returns_none_when_manifest_member_absent(
        self, tmp_path: Path
    ):
        """No manifest.json member at all: the scan loop finishes without ever
        matching, and the function must fall through to its final `return None`."""
        import gzip
        import io
        import tarfile

        archive = tmp_path / "printstash-backup-20200101-000000-cafecafecafe.tar.gz"
        with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            data = b"fake db bytes"
            info = tarfile.TarInfo(name="db.sqlite3")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        assert backup._read_manifest(archive) is None


class TestGetBackupArchivePath:
    def test_get_backup_archive_path_raises_for_unknown_id(self, backup_env: BackupEnv):
        with pytest.raises(FileNotFoundError):
            backup.get_backup_archive_path("does-not-exist")


class TestVerifyBackup:
    def test_verify_backup_accepts_an_intact_archive(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Verified", content=b"solid verified\n")
        meta = backup.create_backup()

        result = backup.verify_backup(meta.id)

        assert result.valid is True
        assert result.app_compatible is True
        assert result.checked_members == 3

    @pytest.mark.parametrize(
        ("mutation", "expected_code", "expected_member"),
        [
            pytest.param(
                partial(_duplicate_named_member, name="manifest.json"),
                "backup_manifest_invalid",
                _manifest_member_name,
                id="duplicate-manifest",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_remove_entry_provider),
                "backup_manifest_invalid",
                _blob_member_name,
                id="missing-evidence",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_duplicate_manifest_entry),
                "backup_manifest_invalid",
                _blob_member_name,
                id="duplicate-declaration",
            ),
            pytest.param(
                _drop_manifest_blob,
                "backup_member_missing",
                _blob_member_name,
                id="missing-member",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_replace_entry_sha256),
                "backup_member_hash_mismatch",
                _blob_member_name,
                id="hash-mismatch",
            ),
        ],
    )
    def test_verify_backup_reports_invalid_archive_evidence(
        self,
        backup_env: BackupEnv,
        mutation: Callable[[BackupEnv, Path, str], None],
        expected_code: str,
        expected_member: Callable[[str], str],
    ) -> None:
        seed_model_with_blob(
            backup_env, name="Verify Invalid", content=b"verify-invalid"
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        member = _archive_manifest(archive)["files"][0]["member"]

        mutation(backup_env, archive, member)

        result = backup.verify_backup(meta.id)

        assert result.valid is False
        assert {
            "code": expected_code,
            "member": expected_member(member),
        } in result.findings


class TestVerifyBackupOwnership:
    @staticmethod
    def _verification(*, valid: bool) -> backup.BackupVerification:
        return backup.BackupVerification(
            backup_id="verified",
            valid=valid,
            app_compatible=True,
            manifest_version="1",
            checked_members=1,
            findings=[] if valid else [{"code": "backup_member_hash_mismatch"}],
        )

    def test_missing_ledger_row_is_reported_without_discovery(self) -> None:
        result = backup.verify_backup_ownership(987654)

        assert result.status == "missing"
        assert result.error == "backup_ownership_not_found"

    @pytest.mark.parametrize(
        ("state", "last_error", "expected_status", "expected_error"),
        [
            pytest.param(
                StorageObjectState.BLOCKED,
                "root_binding_mismatch",
                "identity",
                "root_binding_mismatch",
                id="blocked-with-detail",
            ),
            pytest.param(
                StorageObjectState.BLOCKED,
                None,
                "identity",
                "backup_ownership_blocked",
                id="blocked-without-detail",
            ),
            pytest.param(
                StorageObjectState.PENDING,
                None,
                "missing",
                "backup_ownership_not_committed",
                id="pending",
            ),
        ],
    )
    def test_noncommitted_receipt_keeps_its_ledger_classification(
        self,
        db_session: Session,
        state: StorageObjectState,
        last_error: str | None,
        expected_status: str,
        expected_error: str,
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            object_kind="backup",
            state=state,
            last_error=last_error,
        )

        result = backup.verify_backup_ownership(row.id)

        assert result.status == expected_status
        assert result.error == expected_error

    def test_nonbackup_receipt_is_never_treated_as_an_archive(
        self, db_session: Session
    ) -> None:
        row = build_owned_storage_object(db_session, object_kind="model")

        result = backup.verify_backup_ownership(row.id)

        assert result.status == "missing"
        assert result.error == "backup_ownership_not_found"

    def test_unavailable_opendal_destination_is_an_identity_failure(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            backend="backup-opendal-gdrive",
            namespace="gdrive/PrintStash",
            key="gdrive/PrintStash/printstash-backups/unavailable.tar.gz",
            object_kind="backup",
            provider_ref="unavailable-profile",
        )
        monkeypatch.setattr(backup, "destination_for_ownership", lambda _row: None)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == "identity"
        assert result.error == "backup_storage_ownership_unverified"

    def test_available_opendal_destination_verifies_its_owned_archive(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            backend="backup-opendal-gdrive",
            namespace="gdrive/PrintStash",
            key="gdrive/PrintStash/printstash-backups/available.tar.gz",
            object_kind="backup",
            provider_ref="available-profile",
        )
        cache = tmp_path / "available.tar.gz"
        verification = self._verification(valid=True)
        cleaned: list[Path] = []
        destination = type("Destination", (), {"location": "opendal:gdrive"})()
        monkeypatch.setattr(
            backup, "destination_for_ownership", lambda _row: destination
        )
        monkeypatch.setattr(backup, "_download_backup_to_local", lambda _meta: cache)
        monkeypatch.setattr(
            backup, "verify_backup", lambda *_args, **_kwargs: verification
        )
        monkeypatch.setattr(backup, "cleanup_backup_cache", cleaned.append)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == "valid"
        assert result.verification is verification
        assert cleaned == [cache]

    def test_unrecognized_archive_name_uses_the_ledger_identity(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "custom-name"
        row = build_owned_storage_object(
            db_session,
            backend="local",
            namespace="local/backups",
            key=str(archive),
            object_kind="backup-legacy",
        )
        assert row.id is not None
        verification = self._verification(valid=True)
        backup_ids: list[str] = []

        def verify(
            backup_id: str, *, archive_path: Path, record_audit: bool
        ) -> backup.BackupVerification:
            backup_ids.append(backup_id)
            assert archive_path == archive
            assert record_audit is False
            return verification

        monkeypatch.setattr(backup, "verify_backup", verify)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == "valid"
        assert backup_ids == [str(row.id)]

    @pytest.mark.parametrize(
        ("valid", "expected_status"),
        [
            pytest.param(True, "valid", id="valid"),
            pytest.param(False, "corrupt", id="corrupt"),
        ],
    )
    def test_local_receipt_reports_archive_verification_status(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        valid: bool,
        expected_status: str,
    ) -> None:
        archive = tmp_path / "printstash-verified.tar.gz"
        row = build_owned_storage_object(
            db_session,
            backend="local",
            namespace="local/backups",
            key=str(archive),
            object_kind="backup",
        )
        verification = self._verification(valid=valid)
        calls: list[tuple[str, Path, bool]] = []

        def verify(
            backup_id: str, *, archive_path: Path, record_audit: bool
        ) -> backup.BackupVerification:
            calls.append((backup_id, archive_path, record_audit))
            return verification

        monkeypatch.setattr(backup, "verify_backup", verify)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == expected_status
        assert result.verification is verification
        assert calls == [("verified", archive, False)]

    @pytest.mark.parametrize(
        ("failure", "expected_status", "expected_error"),
        [
            pytest.param(
                FileNotFoundError("gone"),
                "missing",
                "FileNotFoundError",
                id="missing",
            ),
            pytest.param(
                backup.BackupOwnershipError("backup_archive_digest_mismatch"),
                "digest",
                "backup_archive_digest_mismatch",
                id="digest",
            ),
            pytest.param(
                backup.BackupOwnershipError("backup_provider_identity_mismatch"),
                "identity",
                "backup_provider_identity_mismatch",
                id="identity",
            ),
            pytest.param(
                PermissionError("denied"),
                "inaccessible",
                "PermissionError",
                id="inaccessible",
            ),
        ],
    )
    def test_remote_receipt_preserves_failure_classification(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        expected_status: str,
        expected_error: str,
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key="printstash-backups/remote.tar.gz",
            object_kind="backup",
            provider_ref="remote-provider",
        )

        def fail(_meta: backup.BackupMeta) -> Path:
            raise failure

        monkeypatch.setattr(backup, "_download_backup_to_local", fail)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == expected_status
        assert result.error == expected_error

    def test_remote_verification_retires_its_downloaded_cache(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key="printstash-backups/remote.tar.gz",
            object_kind="backup",
            provider_ref="remote-provider",
        )
        cache = tmp_path / "remote-cache.tar.gz"
        verification = self._verification(valid=True)
        cleaned: list[Path] = []
        monkeypatch.setattr(backup, "_download_backup_to_local", lambda _meta: cache)
        monkeypatch.setattr(
            backup, "verify_backup", lambda *_args, **_kwargs: verification
        )
        monkeypatch.setattr(backup, "cleanup_backup_cache", cleaned.append)

        result = backup.verify_backup_ownership(row.id)

        assert result.status == "valid"
        assert cleaned == [cache]


class TestDeleteBackup:
    def test_delete_backup_removes_archive(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()
        assert Path(meta.path).exists()

        assert backup.delete_backup(meta.id) is True
        assert not Path(meta.path).exists()
        assert backup.get_backup(meta.id) is None

    def test_delete_backup_removes_an_owned_opendal_archive(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        namespace = "gdrive/PrintStash"
        key = "gdrive/PrintStash/printstash-backups/remote.tar.gz"
        provider_ref = "saved-profile"
        row = build_owned_storage_object(
            db_session,
            backend="backup-opendal-gdrive",
            namespace=namespace,
            key=key,
            object_kind="backup",
            provider_ref=provider_ref,
            sha256="a" * 64,
        )
        ownership_id = row.id
        source_ref = backup._source_ref(
            location="opendal:gdrive",
            namespace=namespace,
            path=key,
            provider_ref=provider_ref,
        )
        meta = backup.BackupMeta(
            id="remote",
            created_at=row.created_at.isoformat(),
            size_bytes=1,
            storage_backend=row.backend,
            file_count=0,
            app_version="unknown",
            path=key,
            location="opendal:gdrive",
            archive_sha256=row.sha256,
            provider_ref=provider_ref,
            source_ref=source_ref,
            namespace=namespace,
        )
        deleted_keys: list[str] = []

        class Destination:
            def require_owned(self, owned: OwnedStorageObject) -> None:
                assert owned.id == ownership_id

            def delete_owned(
                self, owned: OwnedStorageObject, *, allow_unversioned: bool = False
            ) -> bool:
                assert allow_unversioned is False
                deleted_keys.append(owned.key)
                return True

        destination = Destination()
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(
            backup, "destination_for_ownership", lambda _row: destination
        )

        assert backup.delete_backup(meta.id, source_ref=source_ref) is True
        assert deleted_keys == [key]
        with backup.get_session_factory().session() as session:
            assert session.get(OwnedStorageObject, ownership_id) is None

    def test_delete_backup_refuses_an_unverified_opendal_delete(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        row = build_owned_storage_object(
            db_session,
            backend="backup-opendal-gdrive",
            namespace="gdrive/PrintStash",
            key="gdrive/PrintStash/printstash-backups/refused.tar.gz",
            object_kind="backup",
            provider_ref="saved-profile",
            sha256="b" * 64,
        )
        meta = backup.BackupMeta(
            id="refused",
            created_at=row.created_at.isoformat(),
            size_bytes=1,
            storage_backend=row.backend,
            file_count=0,
            app_version="unknown",
            path=row.key,
            location="opendal:gdrive",
            archive_sha256=row.sha256,
            provider_ref=row.provider_ref,
            namespace=row.namespace,
        )

        class Destination:
            def delete_owned(
                self, _owned: OwnedStorageObject, *, allow_unversioned: bool = False
            ) -> bool:
                assert allow_unversioned is False
                return False

        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(backup, "_require_backup_archive_owned", lambda _meta: row)
        monkeypatch.setattr(
            backup, "destination_for_ownership", lambda _row: Destination()
        )

        with pytest.raises(
            backup.BackupDeleteUnsupportedError, match="backup_exact_delete_unsupported"
        ):
            backup.delete_backup(meta.id)

    def test_delete_backup_refuses_s3_when_its_target_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        meta = backup.BackupMeta(
            id="unavailable-s3",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=1,
            storage_backend="backup-s3",
            file_count=0,
            app_version="unknown",
            path="printstash-backups/unavailable-s3.tar.gz",
            location="s3",
        )
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: None)

        with pytest.raises(
            backup.BackupOwnershipError,
            match="backup_storage_ownership_unverified",
        ):
            backup.delete_backup(meta.id)

    def test_delete_backup_aborts_on_permission_error(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        # Removing a file requires write on its parent dir, not the file itself.
        backup_env.backup_dir.chmod(0o500)
        try:
            with pytest.raises(
                backup.BackupOwnershipError,
                match="backup_storage_ownership_unverified",
            ):
                backup.delete_backup(meta.id)
        finally:
            backup_env.backup_dir.chmod(0o700)

        assert Path(meta.path).exists()

        # Cleanup so tmp_path teardown can remove the archive.
        backup.delete_backup(meta.id)

    def test_cleanup_removes_only_the_owned_cloud_cache(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Cache consumer", content=b"x")
        meta = backup.create_backup()
        cache = backup_env.backup_dir / ".cloud-cache" / "source-ref-archive.tar.gz"
        cache.parent.mkdir(parents=True)
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(cache),
                b"cache-bytes",
                object_kind="backup-cloud-cache",
            )

        backup.cleanup_backup_cache(cache)

        assert not cache.exists()
        assert Path(meta.path).exists()

    def test_cleanup_keeps_a_cache_pinned_by_restore_journal(
        self, backup_env: BackupEnv
    ) -> None:
        cache = backup_env.backup_dir / ".cloud-cache" / "pinned-archive.tar.gz"
        cache.parent.mkdir(parents=True)
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(cache),
                b"pinned-cache",
                object_kind="backup-cloud-cache",
            )
        journal = backup_env.backup_dir / ".restore-pinned.journal"
        provider_ref = backup._restore_provider_ref()
        journal.write_text(
            "\n".join(
                json.dumps(event, sort_keys=True)
                for event in (
                    {
                        "event": "started",
                        "version": 2,
                        "backup_id": "pinned",
                        "archive_sha256": "a" * 64,
                        "operation_nonce": "b" * 64,
                        "backend": "local",
                        "namespaces": [],
                        "provider_ref": provider_ref,
                    },
                    {
                        "event": "cache_pinned",
                        "cache_path": str(cache),
                        "backup_id": "pinned",
                        "operation_nonce": "b" * 64,
                        "archive_sha256": "a" * 64,
                        "provider_ref": provider_ref,
                    },
                )
            )
            + "\n"
        )

        backup.cleanup_backup_cache(cache)

        assert cache.exists()

    @requires_s3
    def test_delete_backup_removes_s3_copy(self, backup_s3_env: BackupEnv):
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup.create_backup()

        s3 = backup._get_backup_s3()
        key = backup._backup_s3_key(Path(meta.path).name)
        assert s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)

        cloud = next(
            source
            for source in backup.list_backup_sources()
            if source.id == meta.id and source.location == "s3"
        )
        assert cloud.source_ref is not None
        assert backup.delete_backup(meta.id, source_ref=cloud.source_ref) is True

        import botocore.exceptions

        with pytest.raises(botocore.exceptions.ClientError):
            s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)


class TestDownloadBackupToLocal:
    def test_closes_the_s3_response_body_after_download(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"s3-download-payload"
        key = "printstash-backups/remote.tar.gz"
        digest = hashlib.sha256(payload).hexdigest()
        body: io.BytesIO | None = None

        class TrackedBody(io.BytesIO):
            closed_by_consumer = False

            def close(self) -> None:
                self.closed_by_consumer = True
                super().close()

        class Store:
            def get_object(self, **_kwargs: object) -> dict[str, object]:
                nonlocal body
                body = TrackedBody(payload)
                return {
                    "Body": body,
                    "ContentLength": len(payload),
                    "ETag": '"download-etag"',
                    "VersionId": "download-version",
                }

            def head_object(self, **_kwargs: object) -> dict[str, object]:
                return {
                    "ContentLength": len(payload),
                    "ETag": '"download-etag"',
                    "VersionId": "download-version",
                }

        owned = OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key=key,
            object_kind="backup",
            state=StorageObjectState.COMMITTED,
            size_bytes=len(payload),
            sha256=digest,
            etag='"download-etag"',
            version_id="download-version",
        )
        meta = backup.BackupMeta(
            id="remote",
            created_at="2020-01-01T00:00:00+00:00",
            size_bytes=len(payload),
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path=key,
            location="s3",
            archive_sha256=digest,
            source_ref="remote-source",
            namespace=owned.namespace,
        )
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: Store())
        monkeypatch.setattr(
            backup, "_require_backup_archive_owned", lambda *_args, **_kwargs: owned
        )

        downloaded = backup._download_backup_to_local(meta)

        assert downloaded.is_file()
        assert downloaded.read_bytes() == payload
        assert body is not None and body.closed_by_consumer is True

    def test_download_backup_to_local_raises_when_local_file_missing(
        self,
        backup_env: BackupEnv,
    ):
        meta = backup.BackupMeta(
            id="ghost",
            created_at="2024-01-01T00:00:00+00:00",
            size_bytes=0,
            storage_backend="local",
            file_count=0,
            app_version="0.0.0",
            path=str(backup_env.backup_dir / "does-not-exist.tar.gz"),
            location="local",
        )
        with pytest.raises(FileNotFoundError):
            backup._download_backup_to_local(meta)


class TestBackupCacheRecovery:
    def test_restore_retires_only_its_owned_cloud_cache(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Remote cache", content=b"remote-cache"
        )
        local_meta = backup.create_backup()
        payload = Path(local_meta.path).read_bytes()
        Path(key).unlink()
        remote_key = "printstash-backups/remote-cache.tar.gz"
        source_ref = "remote-source"
        digest = hashlib.sha256(payload).hexdigest()
        remote = OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key=remote_key,
            provider_ref="remote-provider",
            object_kind="backup",
            state=StorageObjectState.COMMITTED,
            token="cache-token",
            size_bytes=len(payload),
            sha256=digest,
            etag='"archive-etag"',
            version_id="version-cache",
        )
        meta = backup.BackupMeta(
            id=local_meta.id,
            created_at=local_meta.created_at,
            size_bytes=len(payload),
            storage_backend="s3",
            file_count=local_meta.file_count,
            app_version=local_meta.app_version,
            path=remote_key,
            location="s3",
            archive_sha256=digest,
            source_ref=source_ref,
            namespace=remote.namespace,
        )
        store = _BackupObjectStore(payload, "cache-token")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setattr(
            backup, "_require_backup_archive_owned", lambda *_args, **_kwargs: remote
        )

        backup.restore_backup(meta.id)

        cache_identity = hashlib.sha256(
            f"{source_ref}\x1f{remote.version_id}".encode("utf-8")
        ).hexdigest()
        cache_path = (
            backup_env.backup_dir
            / ".cloud-cache"
            / f"{cache_identity}-remote-cache.tar.gz"
        )
        assert not cache_path.exists()
        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.backend == "backup-s3",
                        OwnedStorageObject.key == remote_key,
                    )
                )
                .one()
                .version_id
                == "version-cache"
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.object_kind == "backup-cloud-cache",
                        OwnedStorageObject.key == str(cache_path),
                    )
                ).first()
                is None
            )

    def test_startup_cache_cleanup_retires_only_exact_stale_owned_cache(
        self, backup_env: BackupEnv
    ) -> None:
        backend = storage_backend.LocalStorageBackend()
        cache_root = backup_env.backup_dir / ".cloud-cache"
        cache_root.mkdir(parents=True)
        stale = cache_root / "stale.tar.gz"
        live = cache_root / "live.tar.gz"
        pinned = cache_root / "pinned.tar.gz"
        unrelated = backup_env.backup_dir / "unrelated" / "cache.tar.gz"
        root_archive = backup_env.backup_dir / "root.tar.gz"
        live_bytes = b"live-cache"
        pinned_bytes = b"pinned-cache"
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                backend,
                str(stale),
                b"stale-cache",
                object_kind="backup-cloud-cache",
            )
            stale_row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == str(stale))
            ).one()
            stale_row.created_at = stale_row.created_at - timedelta(days=2)
            session.add(stale_row)
            store_owned_bytes(
                session,
                backend,
                str(live),
                live_bytes,
                object_kind="backup-cloud-cache",
            )
            store_owned_bytes(
                session,
                backend,
                str(pinned),
                pinned_bytes,
                object_kind="backup-cloud-cache",
            )
            build_owned_storage_object(
                session,
                backend="local",
                namespace=backend.namespace_for(str(unrelated)),
                key=str(unrelated),
                object_kind="backup-cloud-cache",
                size_bytes=1,
                sha256=hashlib.sha256(b"x").hexdigest(),
            )
            store_owned_bytes(
                session, backend, str(root_archive), b"root", object_kind="backup"
            )
        pinned_journal = backup_env.backup_dir / ".restore-pinned.journal"
        pinned_journal.write_text(
            "\n".join(
                json.dumps(event, sort_keys=True)
                for event in (
                    {
                        "event": "started",
                        "version": 2,
                        "backup_id": "pinned",
                        "archive_sha256": "a" * 64,
                        "operation_nonce": "b" * 64,
                        "backend": "local",
                        "namespaces": [],
                    },
                    {
                        "event": "cache_pinned",
                        "cache_path": str(pinned),
                        "backup_id": "pinned",
                        "operation_nonce": "b" * 64,
                        "archive_sha256": "a" * 64,
                    },
                )
            )
            + "\n"
        )

        assert backup.reconcile_backup_caches() == 2

        assert not stale.exists()
        assert live.read_bytes() == live_bytes
        assert pinned.read_bytes() == pinned_bytes
        assert not unrelated.exists()
        assert root_archive.read_bytes() == b"root"
        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(stale)
                    )
                ).first()
                is None
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(live)
                    )
                )
                .one()
                .state
                is StorageObjectState.COMMITTED
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(pinned)
                    )
                )
                .one()
                .state
                is StorageObjectState.COMMITTED
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(unrelated)
                    )
                )
                .one()
                .state
                is StorageObjectState.BLOCKED
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.key == str(root_archive)
                    )
                )
                .one()
                .object_kind
                == "backup"
            )

    def test_missing_cache_receipt_is_retired_without_touching_other_rows(
        self, backup_env: BackupEnv
    ) -> None:
        cache_root = backup_env.backup_dir / ".cloud-cache"
        cache_root.mkdir(parents=True)
        missing = cache_root / "missing.tar.gz"
        root_archive = backup_env.backup_dir / "root.tar.gz"
        with backup_env.new_session() as session:
            missing_row = build_owned_storage_object(
                session,
                backend="local",
                namespace=storage_backend.LocalStorageBackend().namespace_for(
                    str(missing)
                ),
                key=str(missing),
                object_kind="backup-cloud-cache",
            )
            root_row = build_owned_storage_object(
                session,
                backend="local",
                namespace=storage_backend.LocalStorageBackend().namespace_for(
                    str(root_archive)
                ),
                key=str(root_archive),
                object_kind="backup",
            )
            missing_id = missing_row.id
            root_id = root_row.id

        assert backup.reconcile_backup_caches() == 1

        with backup_env.new_session() as session:
            assert session.get(OwnedStorageObject, missing_id) is None
            assert session.get(OwnedStorageObject, root_id) is not None

    @pytest.mark.parametrize(
        ("size_bytes", "sha256", "expected_error"),
        [
            pytest.param(None, None, "backup_cache_evidence_missing", id="no-proof"),
            pytest.param(5, "0" * 64, "StorageCollisionError", id="wrong-digest"),
        ],
    )
    def test_unprovable_cache_is_blocked_without_deleting_bytes(
        self,
        backup_env: BackupEnv,
        size_bytes: int | None,
        sha256: str | None,
        expected_error: str,
    ) -> None:
        cache_root = backup_env.backup_dir / ".cloud-cache"
        cache_root.mkdir(parents=True)
        cache = cache_root / "unprovable.tar.gz"
        cache.write_bytes(b"cache")
        with backup_env.new_session() as session:
            row = build_owned_storage_object(
                session,
                backend="local",
                namespace=storage_backend.LocalStorageBackend().namespace_for(
                    str(cache)
                ),
                key=str(cache),
                object_kind="backup-cloud-cache",
                state=StorageObjectState.PENDING,
                size_bytes=size_bytes,
                sha256=sha256,
            )
            row_id = row.id

        assert backup.reconcile_backup_caches() == 0

        assert cache.read_bytes() == b"cache"
        with backup_env.new_session() as session:
            row = session.get(OwnedStorageObject, row_id)
            assert row is not None
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == expected_error

    def test_pending_cache_with_exact_proof_becomes_committed(
        self, backup_env: BackupEnv
    ) -> None:
        cache_root = backup_env.backup_dir / ".cloud-cache"
        cache_root.mkdir(parents=True)
        cache = cache_root / "pending.tar.gz"
        payload = b"pending-cache"
        cache.write_bytes(payload)
        with backup_env.new_session() as session:
            row = build_owned_storage_object(
                session,
                backend="local",
                namespace=storage_backend.LocalStorageBackend().namespace_for(
                    str(cache)
                ),
                key=str(cache),
                object_kind="backup-cloud-cache",
                state=StorageObjectState.PENDING,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            row_id = row.id

        assert backup.reconcile_backup_caches() == 1

        with backup_env.new_session() as session:
            row = session.get(OwnedStorageObject, row_id)
            assert row is not None
            assert row.state is StorageObjectState.COMMITTED
            assert row.committed_at is not None

    def test_stale_cache_delete_failure_preserves_bytes_for_recovery(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = storage_backend.LocalStorageBackend()
        cache_root = backup_env.backup_dir / ".cloud-cache"
        cache_root.mkdir(parents=True)
        cache = cache_root / "stale-failure.tar.gz"
        payload = b"recoverable-cache"
        with backup_env.new_session() as session:
            store_owned_bytes(
                session,
                backend,
                str(cache),
                payload,
                object_kind="backup-cloud-cache",
            )
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == str(cache))
            ).one()
            row.created_at = row.created_at - timedelta(days=2)
            row_id = row.id
            session.add(row)
            session.commit()

        def fail_delete(*_args: object, **_kwargs: object) -> bool:
            raise OSError("storage temporarily unavailable")

        monkeypatch.setattr(backup, "delete_owned_key", fail_delete)

        assert backup.reconcile_backup_caches() == 0

        assert cache.read_bytes() == payload
        with backup_env.new_session() as session:
            row = session.get(OwnedStorageObject, row_id)
            assert row is not None
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "OSError"

    def test_malformed_restore_journal_never_pins_an_unrelated_cache(
        self, backup_env: BackupEnv
    ) -> None:
        journal = backup_env.backup_dir / ".restore-corrupt.journal"
        journal.write_text("not-json\n", encoding="utf-8")

        assert backup._cache_path_pinned_by_restore_journal("unrelated") is False

    def test_ownership_queries_keep_provider_namespaces_separate(
        self, backup_env: BackupEnv
    ) -> None:
        with backup_env.new_session() as session:
            first = build_owned_storage_object(
                session,
                backend="backup-s3",
                namespace="first/printstash-backups/",
                key="printstash-backups/shared.tar.gz",
                provider_ref="first-provider",
                object_kind="backup",
            )
            build_owned_storage_object(
                session,
                backend="backup-s3",
                namespace="second/printstash-backups/",
                key="printstash-backups/shared.tar.gz",
                provider_ref="second-provider",
                object_kind="backup",
            )
            first_key = first.key

        assert backup._committed_backup_keys(
            "backup-s3",
            namespace="first/printstash-backups/",
            provider_ref="first-provider",
        ) == {first_key}
        rows = backup._backup_ownership_rows(
            key="printstash-backups/shared.tar.gz", bucket="first"
        )
        assert [row.provider_ref for row in rows] == ["first-provider"]

    def test_unresolved_restore_pins_cache_with_both_receipts(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Pinned cache", content=b"pinned-cache"
        )
        local_meta = backup.create_backup()
        payload = Path(local_meta.path).read_bytes()
        Path(key).unlink()
        remote_key = "printstash-backups/pinned-cache.tar.gz"
        source_ref = "pinned-source"
        digest = hashlib.sha256(payload).hexdigest()
        remote = OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key=remote_key,
            provider_ref="remote-provider",
            object_kind="backup",
            state=StorageObjectState.COMMITTED,
            token="cache-token",
            size_bytes=len(payload),
            sha256=digest,
            etag='"archive-etag"',
            version_id="version-pinned",
        )
        meta = backup.BackupMeta(
            id=local_meta.id,
            created_at=local_meta.created_at,
            size_bytes=len(payload),
            storage_backend="s3",
            file_count=local_meta.file_count,
            app_version=local_meta.app_version,
            path=remote_key,
            location="s3",
            archive_sha256=digest,
            source_ref=source_ref,
            namespace=remote.namespace,
        )
        store = _BackupObjectStore(payload, "cache-token")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "get_backup", lambda *_args, **_kwargs: meta)
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setattr(
            backup, "_require_backup_archive_owned", lambda *_args, **_kwargs: remote
        )
        real_append = backup._append_restore_journal

        def interrupt_after_active(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "database_active":
                raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_append_restore_journal", interrupt_after_active)
        try:
            with pytest.raises(KeyboardInterrupt):
                backup.restore_backup(meta.id)
        finally:
            backup._end_restore_maintenance()

        cache_identity = hashlib.sha256(
            f"{source_ref}\x1f{remote.version_id}".encode("utf-8")
        ).hexdigest()
        cache_path = (
            backup_env.backup_dir
            / ".cloud-cache"
            / f"{cache_identity}-pinned-cache.tar.gz"
        )
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        assert cache_path.exists()
        journal_events = [json.loads(line) for line in journal.read_text().splitlines()]
        assert any(
            event.get("event") == "cache_pinned"
            and event.get("cache_path") == str(cache_path)
            for event in journal_events
        )
        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.backend == "backup-s3",
                        OwnedStorageObject.key == remote_key,
                    )
                )
                .one()
                .version_id
                == "version-pinned"
            )
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.object_kind == "backup-cloud-cache",
                        OwnedStorageObject.key == str(cache_path),
                    )
                )
                .one()
                .sha256
                == digest
            )

    def test_cache_path_encodes_complete_safe_source_identity(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"cache-identity"
        digest = hashlib.sha256(payload).hexdigest()
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        paths: list[Path] = []
        for source_ref, version_id in (
            ("source/one", "version-1"),
            ("source/two", "version-2"),
        ):
            store = _BackupObjectStore(payload, "identity-token")
            real_head = store.head_object
            real_get = store.get_object

            def head_with_version(
                _head=real_head, _version=version_id, **kwargs: object
            ) -> dict[str, object]:
                response = _head(**kwargs)
                response["VersionId"] = _version
                return response

            def get_with_version(
                _get=real_get, _version=version_id, **kwargs: object
            ) -> dict[str, object]:
                response = _get(**kwargs)
                response["VersionId"] = _version
                return response

            store.head_object = head_with_version  # type: ignore[method-assign]
            store.get_object = get_with_version  # type: ignore[method-assign]
            monkeypatch.setattr(backup, "_get_backup_s3", lambda s=store: s)
            remote = OwnedStorageObject(
                backend="backup-s3",
                namespace="archive-bucket/printstash-backups/",
                key="printstash-backups/../unsafe.tar.gz",
                provider_ref="remote-provider",
                object_kind="backup",
                state=StorageObjectState.COMMITTED,
                token="identity-token",
                size_bytes=len(payload),
                sha256=digest,
                etag='"archive-etag"',
                version_id=version_id,
            )
            meta = backup.BackupMeta(
                id=f"identity-{version_id}",
                created_at="2026-01-01T00:00:00+00:00",
                size_bytes=len(payload),
                storage_backend="s3",
                file_count=0,
                app_version="0.13.0",
                path=remote.key,
                location="s3",
                archive_sha256=digest,
                source_ref=source_ref,
                namespace=remote.namespace,
            )
            monkeypatch.setattr(
                backup,
                "_require_backup_archive_owned",
                lambda *_args, r=remote, **_kwargs: r,
            )
            paths.append(backup._download_backup_to_local(meta))
        assert paths[0] != paths[1]
        assert all(
            path.parent == backup_env.backup_dir / ".cloud-cache" for path in paths
        )
        assert all(path.name.endswith("-unsafe.tar.gz") for path in paths)

    def test_matching_unowned_cache_collision_is_preserved(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"unowned-cache"
        digest = hashlib.sha256(payload).hexdigest()
        source_ref = "collision-source"
        version_id = "collision-version"
        identity = hashlib.sha256(
            f"{source_ref}\x1f{version_id}".encode("utf-8")
        ).hexdigest()
        cache_path = (
            backup_env.backup_dir / ".cloud-cache" / f"{identity}-collision.tar.gz"
        )
        cache_path.parent.mkdir(parents=True)
        cache_path.write_bytes(payload)
        remote = OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key="printstash-backups/collision.tar.gz",
            provider_ref="remote-provider",
            object_kind="backup",
            state=StorageObjectState.COMMITTED,
            token="collision-token",
            size_bytes=len(payload),
            sha256=digest,
            etag='"archive-etag"',
            version_id=version_id,
        )
        meta = backup.BackupMeta(
            id="collision",
            created_at="2026-01-01T00:00:00+00:00",
            size_bytes=len(payload),
            storage_backend="s3",
            file_count=0,
            app_version="0.13.0",
            path=remote.key,
            location="s3",
            archive_sha256=digest,
            source_ref=source_ref,
            namespace=remote.namespace,
        )
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(
            backup,
            "_get_backup_s3",
            lambda: _BackupObjectStore(payload, "collision-token"),
        )
        monkeypatch.setattr(
            backup, "_require_backup_archive_owned", lambda *_args, **_kwargs: remote
        )

        with pytest.raises(
            backup.BackupOwnershipError, match="backup_cache_ownership_unverified"
        ):
            backup._download_backup_to_local(meta)

        assert cache_path.read_bytes() == payload
        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.object_kind == "backup-cloud-cache",
                        OwnedStorageObject.key == str(cache_path),
                    )
                ).first()
                is None
            )


class TestDownloadBackupEndpoints:
    def test_download_backup_archive_endpoint(
        self, client: TestClient, backup_env: BackupEnv
    ):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        resp = client.get(
            f"/api/v1/backups/{meta.id}/download",
            headers=_auth_headers(backup_env),
        )

        assert resp.status_code == 200, resp.text
        assert resp.content.startswith(b"\x1f\x8b")
        assert Path(meta.path).name in resp.headers["content-disposition"]

    def test_download_then_restore_endpoint_round_trip(
        self, client: TestClient, backup_env: BackupEnv
    ):
        content = b"solid endpoint widget\nendsolid\n"
        _model_id, key = seed_model_with_blob(
            backup_env, name="Endpoint Widget", content=content
        )
        headers = _auth_headers(backup_env)

        create = client.post("/api/v1/backups", headers=headers)
        assert create.status_code == 202, create.text
        backup_id = create.json()["backup_id"]

        download = client.get(f"/api/v1/backups/{backup_id}/download", headers=headers)
        assert download.status_code == 200, download.text
        assert download.content.startswith(b"\x1f\x8b")
        assert f"{backup_id}.tar.gz" in download.headers["content-disposition"]

        # Disaster: remove both catalog row and stored bytes, then restore via API.
        with backup_env.new_session() as session:
            for m in session.exec(select(Model).where(Model.name == "Endpoint Widget")):
                session.delete(m)
            session.commit()
        Path(key).unlink()

        assert "Endpoint Widget" not in _read_model_names(backup_env)
        assert not Path(key).exists()

        restore = client.post(f"/api/v1/backups/{backup_id}/restore", headers=headers)
        assert restore.status_code == 200, restore.text
        assert restore.json() == {"backup_id": backup_id, "restored_files": 1}

        assert "Endpoint Widget" in _read_model_names(backup_env)
        assert Path(key).read_bytes() == content

    @requires_s3
    def test_restore_downloads_s3_only_backup_before_restoring(
        self, backup_s3_env: BackupEnv
    ):
        _model_id, key = seed_model_with_blob(
            backup_s3_env, name="Widget", content=b"solid widget\n"
        )
        meta = backup.create_backup()
        # Simulate the data-loss case that restore is allowed to repair. Restoring
        # over a live destination must remain a conflict, even when the archive is
        # downloaded from S3.
        Path(key).unlink()
        Path(meta.path).unlink()

        result = backup.restore_backup(meta.id)

        assert result["backup_id"] == meta.id
        assert _read_model_names(backup_s3_env) == ["Widget"]
        assert Path(key).read_bytes() == b"solid widget\n"
        # The source-specific cache is a rebuildable derivative and is cleaned
        # after a successful restore.
        assert not list((backup_s3_env.backup_dir / ".cloud-cache").glob("*.tar.gz"))


class TestHasMember:
    def test_has_member_false_for_missing_entry(self, backup_env: BackupEnv):
        import tarfile

        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        with tarfile.open(Path(meta.path), mode="r:gz") as tar:
            assert backup._has_member(tar, "manifest.json") is True
            assert backup._has_member(tar, "nonexistent.entry") is False


class TestRestoreKeyMap:
    def test_restore_key_map_empty_for_legacy_archive_without_manifest(
        self, tmp_path: Path
    ):
        import io
        import tarfile

        archive = tmp_path / "legacy.tar"
        with tarfile.open(archive, mode="w") as tar:
            data = b"not a real db"
            info = tarfile.TarInfo(name="db.sqlite3")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        with tarfile.open(archive, mode="r") as tar:
            assert backup._restore_key_map(tar) == {}

    def test_restore_key_map_empty_for_corrupt_manifest_json(self, tmp_path: Path):
        import io
        import tarfile

        archive = tmp_path / "corrupt-manifest.tar"
        with tarfile.open(archive, mode="w") as tar:
            data = b"{not valid json"
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        with tarfile.open(archive, mode="r") as tar:
            assert backup._restore_key_map(tar) == {}

    def test_restore_key_map_empty_when_manifest_entry_is_a_directory(
        self, tmp_path: Path
    ):
        import tarfile

        archive = tmp_path / "dir-manifest.tar"
        with tarfile.open(archive, mode="w") as tar:
            info = tarfile.TarInfo(name="manifest.json")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)

        with tarfile.open(archive, mode="r") as tar:
            assert backup._restore_key_map(tar) == {}

    def test_restore_key_map_empty_when_manifest_member_unreadable(
        self, tmp_path: Path
    ):
        """Same "member present but not a regular file" case as _read_manifest,
        but for the restore-side key map."""
        import tarfile

        archive = tmp_path / "weird.tar"
        with tarfile.open(archive, mode="w") as tar:
            info = tarfile.TarInfo(name="manifest.json")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)

        with tarfile.open(archive, mode="r") as tar:
            assert backup._restore_key_map(tar) == {}


class TestStageRestoreArchive:
    @pytest.mark.parametrize(
        ("mutation", "error"),
        [
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_set_unsupported_version),
                "backup_manifest_invalid",
                id="unsupported-version",
            ),
            pytest.param(
                partial(_duplicate_named_member, name="manifest.json"),
                "backup_manifest_invalid",
                id="duplicate-manifest",
            ),
            pytest.param(
                partial(_drop_named_member, name="db.sqlite3"),
                "backup_manifest_invalid",
                id="missing-database",
            ),
            pytest.param(
                partial(_duplicate_named_member, name="db.sqlite3"),
                "backup_manifest_invalid",
                id="duplicate-database",
            ),
            pytest.param(
                partial(
                    _rewrite_manifest_with,
                    mutation=_replace_entry_with_invalid_shape,
                ),
                "backup_manifest_invalid",
                id="malformed-entry",
            ),
            pytest.param(
                partial(_rewrite_manifest_with, mutation=_duplicate_manifest_entry),
                "backup_manifest_invalid",
                id="duplicate-entry",
            ),
            pytest.param(
                partial(_add_named_member, name="../escape", content=b"unsafe"),
                "backup_manifest_invalid",
                id="unsafe-member",
            ),
            pytest.param(
                partial(_add_named_member, name="notes.txt", content=b"unlisted"),
                "backup_manifest_invalid",
                id="unlisted-member",
            ),
            pytest.param(
                partial(
                    _rewrite_manifest_with, mutation=_increment_manifest_file_count
                ),
                "backup_manifest_invalid",
                id="file-count",
            ),
            pytest.param(
                partial(
                    _rewrite_manifest_with, mutation=_increment_manifest_entry_size
                ),
                "backup_member_size_mismatch",
                id="member-size",
            ),
        ],
    )
    def test_restore_rejects_invalid_archive_structure(
        self,
        backup_env: BackupEnv,
        mutation: Callable[[BackupEnv, Path, str], None],
        error: str,
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Restore Invalid", content=b"restore-structure"
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        member = _archive_manifest(archive)["files"][0]["member"]
        mutation(backup_env, archive, member)
        Path(key).unlink()

        with pytest.raises(RuntimeError, match=error):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_accepts_a_v1_archive(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"legacy-v1"
        _, key = seed_model_with_blob(backup_env, name="Legacy", content=content)
        meta = backup.create_backup()
        archive = Path(meta.path)
        # Build a genuine pre-v2 archive shape from explicit released fields.
        # This deliberately does not rewrite a current manifest: v0.12.1 had
        # no provider/namespace/hash metadata in its file entries.
        database_bytes = backup_env.db_file.read_bytes()
        member = "files/legacy/legacy.stl"
        manifest = {
            "version": "1",
            "created_at": meta.created_at,
            "app_version": "0.12.1",
            "storage_backend": "local",
            "files": [{"member": member, "key": key, "size": len(content)}],
        }
        with tarfile.open(archive, mode="w:gz") as tar:
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_bytes)
            tar.addfile(manifest_info, io.BytesIO(manifest_bytes))
            database_info = tarfile.TarInfo(name="db.sqlite3")
            database_info.size = len(database_bytes)
            tar.addfile(database_info, io.BytesIO(database_bytes))
            blob_info = tarfile.TarInfo(name=member)
            blob_info.size = len(content)
            tar.addfile(blob_info, io.BytesIO(content))

        # The archive ledger must describe the literal fixture bytes, as it
        # would after a crash between publication and ownership commit.
        stat = archive.stat()
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(archive),
                    OwnedStorageObject.object_kind == "backup",
                )
            ).one()
            row.size_bytes = stat.st_size
            row.sha256 = backup._sha256_path(archive)
            row.etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
            row.device = stat.st_dev
            row.inode = stat.st_ino
            row.ctime_ns = stat.st_ctime_ns
            session.add(row)
            session.commit()
        Path(key).unlink()
        # The fixture is deliberately a literal v1 archive, while the harness
        # database is created without Alembic's historical index backfills.
        # Keep this test focused on v1 archive compatibility; migration parity
        # is covered by the released-revision migration fixtures.
        monkeypatch.setattr(backup, "run_migrations", lambda _url: None)

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_rejects_a_missing_v2_member(self, backup_env: BackupEnv) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Missing Member", content=b"missing-member"
        )
        meta = backup.create_backup()
        with tarfile.open(meta.path, mode="r:gz") as archive:
            stream = archive.extractfile("manifest.json")
            assert stream is not None
            member = json.loads(stream.read())["files"][0]["member"]
        _rewrite_backup_archive(
            backup_env, Path(meta.path), lambda _manifest: None, drop_member=member
        )
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_manifest_invalid"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_rejects_a_duplicate_v2_member(self, backup_env: BackupEnv) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Duplicate Member", content=b"duplicate-member"
        )
        meta = backup.create_backup()
        with tarfile.open(meta.path, mode="r:gz") as archive:
            stream = archive.extractfile("manifest.json")
            assert stream is not None
            member = json.loads(stream.read())["files"][0]["member"]
        _rewrite_backup_archive(
            backup_env,
            Path(meta.path),
            lambda _manifest: None,
            duplicate_member=member,
        )
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_manifest_invalid"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_rejects_an_unlisted_v2_member(self, backup_env: BackupEnv) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Unlisted Member", content=b"listed-member"
        )
        meta = backup.create_backup()
        _rewrite_backup_archive(
            backup_env,
            Path(meta.path),
            lambda _manifest: None,
            extra_member=("files/unlisted.bin", b"unlisted"),
        )
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_manifest_invalid"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_rejects_tampered_v2_bytes(self, backup_env: BackupEnv) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Tampered", content=b"original-bytes"
        )
        meta = backup.create_backup()

        def replace_hash(manifest: dict) -> None:
            manifest["files"][0]["sha256"] = "0" * 64

        _rewrite_backup_archive(backup_env, Path(meta.path), replace_hash)
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_member_hash_mismatch"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("provider", "foreign", id="provider"),
            pytest.param("namespace", "data:/foreign-vault", id="namespace"),
        ],
    )
    def test_restore_rejects_foreign_storage_identity_before_writing(
        self, backup_env: BackupEnv, field: str, value: str
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Foreign Identity", content=b"foreign-identity"
        )
        meta = backup.create_backup()

        def replace_identity(manifest: dict) -> None:
            manifest["files"][0][field] = value

        _rewrite_backup_archive(backup_env, Path(meta.path), replace_identity)
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_storage_namespace_mismatch"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_rejects_a_destination_outside_storage(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Outside Destination", content=b"outside-destination"
        )
        meta = backup.create_backup()
        outside = backup_env.root / "outside-storage.bin"

        def replace_key(manifest: dict) -> None:
            manifest["files"][0]["key"] = str(outside)

        _rewrite_backup_archive(backup_env, Path(meta.path), replace_key)
        Path(key).unlink()

        with pytest.raises(RuntimeError, match="backup_restore_key_outside_storage"):
            backup.restore_backup(meta.id)

        assert not outside.exists()


class TestRestoreDatabase:
    def test_sync_restored_ownership_preserves_exact_provider_identity(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Provider siblings", content=b"restored"
        )
        backend = get_backend()
        namespace = backend.namespace_for(key)
        current_provider = backup.provider_ref_for_backend(backend, namespace=namespace)
        old_provider = "f" * 64
        receipt = storage_backend.CreationReceipt(
            key=key,
            size=len(b"restored"),
            token="restore-token",
            backend=backend.backend_name,
            namespace=namespace,
            device=1,
            inode=2,
            ctime_ns=3,
        )
        applied = [
            backup._AppliedBlob(
                key=key,
                receipt=receipt,
                sha256=hashlib.sha256(b"restored").hexdigest(),
                generation=1,
            )
        ]
        archive = OwnedStorageObject(
            backend="backup-s3",
            namespace="archive-bucket/printstash-backups/",
            key="printstash-backups/provider-siblings.tar.gz",
            provider_ref="a" * 64,
            object_kind="backup",
            state=StorageObjectState.COMMITTED,
            token="archive-token",
            size_bytes=10,
            sha256="b" * 64,
        )
        with backup_env.new_session() as session:
            build_owned_storage_object(
                session,
                backend=backend.backend_name,
                namespace=namespace,
                key=key,
                provider_ref=old_provider,
                object_kind="old-provider",
                size_bytes=8,
                sha256="c" * 64,
            )
            build_owned_storage_object(
                session,
                backend=backend.backend_name,
                namespace=namespace,
                key=key,
                provider_ref=current_provider,
                object_kind="current-provider",
                size_bytes=7,
                sha256="d" * 64,
            )

        backup._sync_restored_ownership(
            backup_env.db_file,
            applied,
            archive_ownership=archive,
        )

        with backup_env.new_session() as session:
            rows = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key.in_([key, archive.key])  # type: ignore[attr-defined]
                )
            ).all()
            siblings = [row for row in rows if row.key == key]
            assert {row.provider_ref for row in siblings} == {
                old_provider,
                current_provider,
            }
            restored = next(
                row for row in siblings if row.provider_ref == current_provider
            )
            assert restored.token == "restore-token"
            assert restored.inode == 2
            archive_row = next(row for row in rows if row.key == archive.key)
            assert archive_row.provider_ref == archive.provider_ref
            assert archive_row.object_kind == "backup"

    def test_start_only_journal_does_not_promote_a_matching_stale_marker(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A marker alone cannot turn a pre-swap journal into active recovery."""
        backup_id = "start-only"
        archive_sha256 = "a" * 64
        operation_nonce = "b" * 64
        journal = backup_env.backup_dir / f".restore-{backup_id}.journal"
        journal.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": backup_id,
                    "archive_sha256": archive_sha256,
                    "operation_nonce": operation_nonce,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n"
        )
        with backup_env.new_session() as session:
            session.add(
                RestoreMarker(
                    backup_id=backup_id,
                    operation_nonce=operation_nonce,
                    archive_sha256=archive_sha256,
                    state="database_active",
                )
            )
            session.commit()

        monkeypatch.setattr(
            backup,
            "_active_restore_marker",
            lambda *_args, **_kwargs: pytest.fail("start-only journal promoted"),
        )
        assert backup.inspect_restore_recovery() is True
        assert backup.restore_in_progress() is True
        state = backup._load_restore_journal(journal)
        assert state.database_swap_intent is False
        assert state.database_active is False

    @pytest.mark.critical
    def test_existing_swap_intent_with_inactive_marker_is_not_duplicated(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Inactive marker", content=b"inactive-marker"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal
        events: list[dict[str, object]] = []

        def interrupt_at_swap(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            events.append(dict(event))
            if event.get("event") == "database_swap_intent":
                raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_append_restore_journal", interrupt_at_swap)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)

        monkeypatch.setattr(backup, "_append_restore_journal", real_append)
        backup.restore_backup(meta.id)

        assert [event["event"] for event in events].count("database_swap_intent") == 1
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        assert not journal.exists()
        assert Path(key).read_bytes() == b"inactive-marker"

    def test_active_marker_ack_is_durable_before_terminal_completion(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Active marker", content=b"active-marker"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def interrupt_at_swap(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "database_swap_intent":
                raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_append_restore_journal", interrupt_at_swap)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)

        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        started = json.loads(journal.read_text().splitlines()[0])
        with backup_env.new_session() as session:
            session.add(
                RestoreMarker(
                    backup_id=meta.id,
                    operation_nonce=started["operation_nonce"],
                    archive_sha256=started["archive_sha256"],
                    state="database_active",
                )
            )
            session.commit()

        resumed_events: list[dict[str, object]] = []

        def record_append(path: Path, event: dict[str, object]) -> None:
            resumed_events.append(dict(event))
            real_append(path, event)

        monkeypatch.setattr(backup, "_append_restore_journal", record_append)
        backup.restore_backup(meta.id)

        names = [event["event"] for event in resumed_events]
        assert names == ["database_active", "complete"]
        assert not journal.exists()
        assert Path(key).read_bytes() == b"active-marker"

    @pytest.mark.parametrize(
        "proof",
        [
            pytest.param("false", id="inactive-marker"),
            pytest.param("unknown", id="unknown-marker"),
        ],
    )
    def test_terminal_journal_requires_matching_active_marker(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        proof: str,
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Terminal marker", content=b"terminal-marker"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_remove = backup._remove_restore_journal

        def leave_terminal_journal(path: Path) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_remove_restore_journal", leave_terminal_journal)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_remove_restore_journal", real_remove)

        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        assert journal.exists()
        if proof == "false":
            with backup_env.new_session() as session:
                marker = session.exec(select(RestoreMarker)).one()
                marker.state = "database_inactive"
                session.add(marker)
                session.commit()
        else:
            monkeypatch.setattr(
                backup, "_active_restore_marker", lambda *_args, **_kwargs: None
            )

        monkeypatch.setattr(
            get_backend(),
            "create_stream",
            lambda *_args, **_kwargs: pytest.fail("terminal restore replayed bytes"),
        )
        with pytest.raises(
            backup.RestoreConflictError, match="restore_database_state_unknown"
        ):
            backup.restore_backup(meta.id)

        assert journal.exists()
        assert Path(key).read_bytes() == b"terminal-marker"

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param("missing", id="missing-published-blob"),
            pytest.param("changed", id="changed-published-blob"),
        ],
    )
    def test_active_swap_journal_rejects_mutated_published_blob(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        mutation: str,
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Active blob", content=b"active-blob"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def interrupt_at_active(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "database_active":
                raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_append_restore_journal", interrupt_at_active)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)

        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        before_journal = journal.read_bytes()
        if mutation == "missing":
            Path(key).unlink()
        else:
            Path(key).write_bytes(b"foreign-active-blob")
        before_bytes = Path(key).read_bytes() if Path(key).exists() else None

        with pytest.raises(
            backup.RestoreConflictError, match="restore_destination_changed"
        ):
            backup.restore_backup(meta.id)

        assert journal.read_bytes() == before_journal
        assert (Path(key).read_bytes() if Path(key).exists() else None) == before_bytes
        backup._end_restore_maintenance()

    def test_backup_round_trips_a_source_cover(self, backup_env: BackupEnv) -> None:
        content = b"private-source-cover"
        with backup_env.new_session() as session:
            model = build_model(session, name="Covered")
            source = build_provenance_source(session, model)
            key = get_backend().source_cover_key(source.id)
            get_backend().write_bytes(content, key)
            build_cover(session, source, storage_key=key, size_bytes=len(content))
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_preserves_ownership_sha256(self, backup_env: BackupEnv) -> None:
        content = b"ownership-evidence"
        _, key = seed_model_with_blob(backup_env, name="Owned", content=content)
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)

        with backup_env.new_session() as session:
            ownership = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
        assert (
            ownership.sha256
            == "a671576405ad7071ea8f8c077e8698c0b795ccf037ca7c12c353d34599deb241"
        )

    def test_restore_database_raises_for_non_file_db(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(backup, "_db_path", lambda: None)
        with pytest.raises(RuntimeError, match="cannot restore to non-file database"):
            backup._restore_database(b"irrelevant")

    def test_restore_recovers_database_rows(self, backup_env: BackupEnv):
        _, key = seed_model_with_blob(
            backup_env, name="Widget", content=b"solid widget\n"
        )
        meta = backup.create_backup()

        # Disaster: wipe every model row.
        with backup_env.new_session() as session:
            for m in session.exec(select(Model)).all():
                session.delete(m)
            session.commit()
        backup_env.engine.dispose()

        assert _read_model_names(backup_env) == []
        Path(key).unlink()

        backup.restore_backup(meta.id)

        assert "Widget" in _read_model_names(backup_env)
        with backup_env.new_session() as session:
            owned = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            receipt = storage_backend.CreationReceipt(
                key=owned.key,
                size=owned.size_bytes,
                token=owned.token,
                backend=owned.backend,
                namespace=owned.namespace,
                etag=owned.etag,
                device=owned.device,
                inode=owned.inode,
                ctime_ns=owned.ctime_ns,
            )
            assert get_backend().creation_matches(receipt)

    def test_restore_refreshes_archive_receipt_after_metadata_only_change(
        self, backup_env: BackupEnv
    ) -> None:
        content = b"metadata-only-recovery"
        _, key = seed_model_with_blob(
            backup_env, name="Metadata recovery", content=content
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        before = archive.stat()

        os.chown(archive, before.st_uid, before.st_gid)
        after = archive.stat()
        assert (after.st_dev, after.st_ino, after.st_size) == (
            before.st_dev,
            before.st_ino,
            before.st_size,
        )
        assert after.st_ctime_ns != before.st_ctime_ns
        Path(key).unlink()

        result = backup.restore_backup(meta.id)

        assert result == {"backup_id": meta.id, "restored_files": 1}
        assert Path(key).read_bytes() == content

    def test_restore_rejects_identical_archive_at_a_replaced_inode(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Replaced archive", content=b"same archive bytes"
        )
        meta = backup.create_backup()
        archive = Path(meta.path)
        original_inode = archive.stat().st_ino
        replacement = archive.with_suffix(".replacement")
        replacement.write_bytes(archive.read_bytes())
        os.replace(replacement, archive)
        assert archive.stat().st_ino != original_inode
        Path(key).unlink()

        with pytest.raises(
            backup.BackupOwnershipError,
            match="backup_storage_ownership_unverified",
        ):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    @pytest.mark.parametrize(
        "missing_field",
        [pytest.param("token", id="token"), pytest.param("size_bytes", id="size")],
    )
    def test_restore_refuses_incomplete_archive_ownership(
        self, backup_env: BackupEnv, missing_field: str
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Incomplete ownership", content=b"owned bytes"
        )
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            ownership = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            setattr(ownership, missing_field, None)
            session.add(ownership)
            session.commit()
        Path(key).unlink()

        with pytest.raises(
            backup.BackupOwnershipError,
            match="backup_storage_ownership_unverified",
        ):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_replaces_live_wal_state_without_replay(
        self, backup_env: BackupEnv
    ):
        _, key = seed_model_with_blob(
            backup_env, name="Widget", content=b"solid widget\n"
        )
        meta = backup.create_backup()

        # Keep a WAL connection open with a committed post-backup change. A raw
        # overwrite of the main DB file lets this WAL replay over the restored DB.
        live = sqlite3.connect(backup_env.db_file)
        try:
            assert live.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
            live.execute(
                "UPDATE models SET name = 'Post Backup State' WHERE name = 'Widget'"
            )
            live.commit()
            assert live.execute("SELECT name FROM models").fetchone() == (
                "Post Backup State",
            )
            assert Path(f"{backup_env.db_file}-wal").exists()
            Path(key).unlink()

            backup.restore_backup(meta.id)

            assert live.execute("SELECT name FROM models").fetchone() == ("Widget",)
            assert _read_model_names(backup_env) == ["Widget"]
        finally:
            live.close()

    def test_restore_recovers_blob_bytes(self, backup_env: BackupEnv):
        content = b"solid widget\nendsolid\n"
        _model_id, key = seed_model_with_blob(
            backup_env, name="Widget", content=content
        )
        meta = backup.create_backup()

        # Disaster: delete the stored blob.
        Path(key).unlink()
        assert not Path(key).exists()

        result = backup.restore_backup(meta.id)

        assert result["restored_files"] == 1
        # The blob the database references must be back, byte-for-byte.
        assert Path(key).exists(), "restored blob is missing at its storage key"
        assert Path(key).read_bytes() == content

    def test_restore_reuses_a_matching_destination(self, backup_env: BackupEnv):
        content = b"solid matching widget\nendsolid\n"
        _model_id, key = seed_model_with_blob(
            backup_env, name="Matching Widget", content=content
        )
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            model = session.exec(
                select(Model).where(Model.name == "Matching Widget")
            ).one()
            model.name = "Post-backup name"
            session.add(model)
            session.commit()

        result = backup.restore_backup(meta.id)

        assert result == {"backup_id": meta.id, "restored_files": 1}
        assert _read_model_names(backup_env) == ["Matching Widget"]
        assert Path(key).read_bytes() == content

    def test_restore_writes_complete_row_on_success(self, backup_env: BackupEnv):
        from app.db.models import AuditLog

        _, key = seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)

        # restore.start was written before the DB swap and lives only in the
        # pre-restore database, which no longer exists after a successful
        # restore — restore.complete is the persisted, post-swap signal.
        with backup_env.new_session() as session:
            rows = session.exec(
                select(AuditLog).where(AuditLog.action == "restore.complete")
            ).all()
        assert len(rows) == 1
        assert rows[0].resource_type == "backup"

    def test_failed_restore_writes_failed_row(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        from app.db.models import AuditLog

        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated failure mid-restore")

        monkeypatch.setattr(backup, "_download_backup_to_local", _boom)

        with pytest.raises(RuntimeError):
            backup.restore_backup(meta.id)

        with backup_env.new_session() as session:
            actions = {row.action for row in session.exec(select(AuditLog)).all()}
        assert "restore.failed" in actions

    def test_a_restore_refused_for_a_collision_changes_nothing(
        self, backup_env: BackupEnv
    ):
        _, first_key = seed_model_with_blob(
            backup_env, name="First", content=b"backup-first"
        )
        _, second_key = seed_model_with_blob(
            backup_env, name="Second", content=b"backup-second"
        )
        meta = backup.create_backup()

        Path(first_key).write_bytes(b"current-first")
        Path(second_key).write_bytes(b"current-second")
        with backup_env.new_session() as session:
            session.exec(
                select(Model).where(Model.name == "First")
            ).one().name = "Current First"
            session.commit()

        with pytest.raises(backup.RestoreConflictError, match="destination_exists"):
            backup.restore_backup(meta.id)

        assert Path(first_key).read_bytes() == b"current-first"
        assert Path(second_key).read_bytes() == b"current-second"
        assert "Current First" in _read_model_names(backup_env)

    def test_restore_collision_removes_its_preflight_journal(
        self, backup_env: BackupEnv
    ):
        _, matching_key = seed_model_with_blob(
            backup_env, name="Matching before collision", content=b"matching"
        )
        _, conflicting_key = seed_model_with_blob(
            backup_env, name="Journal collision", content=b"backup"
        )
        meta = backup.create_backup()
        Path(conflicting_key).write_bytes(b"current")
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"

        with pytest.raises(backup.RestoreConflictError, match="destination_exists"):
            backup.restore_backup(meta.id)

        assert Path(matching_key).read_bytes() == b"matching"
        assert not journal.exists()

    def test_restore_collision_releases_the_mutation_gate(self, backup_env: BackupEnv):
        _, key = seed_model_with_blob(
            backup_env, name="Gate collision", content=b"backup"
        )
        meta = backup.create_backup()
        Path(key).write_bytes(b"current")

        with pytest.raises(backup.RestoreConflictError, match="destination_exists"):
            backup.restore_backup(meta.id)

        assert backup.restore_in_progress() is False
        assert backup.begin_mutating_operation() is True
        backup.end_mutating_operation()

    def test_failed_blob_restore_removes_only_receipted_partial_creates(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        _, first_key = seed_model_with_blob(
            backup_env, name="First", content=b"backup-first"
        )
        _, second_key = seed_model_with_blob(
            backup_env, name="Second", content=b"backup-second"
        )
        meta = backup.create_backup()
        Path(first_key).unlink()
        Path(second_key).unlink()

        backend = get_backend()
        real_create = backend.create_stream
        writes = 0

        def fail_second_write(source, key: str):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("simulated second blob failure")
            return real_create(source, key)

        monkeypatch.setattr(backend, "create_stream", fail_second_write)

        with pytest.raises(OSError, match="second blob failure"):
            backup.restore_backup(meta.id)

        assert not Path(first_key).exists()
        assert not Path(second_key).exists()

    def test_restore_rejects_a_duplicate_destination_before_publish(
        self, backup_env: BackupEnv
    ) -> None:
        content = b"duplicate-destination"
        staged = backup_env.root / "staged-duplicate.bin"
        staged.write_bytes(content)
        key = str(backup_env.data_dir / "duplicate-destination.bin")
        blob = backup._StagedBlob(
            key=key,
            path=staged,
            size=len(content),
            sha256="0" * 64,
            namespace=get_backend().namespace_for(key),
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_duplicate_destination"
        ):
            backup._apply_staged_blobs([blob, blob], backup_env.root / "rollback")

        assert not Path(key).exists()

    def test_restore_rolls_back_blob_when_database_restore_fails(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Database Failure", content=b"database-failure"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        monkeypatch.setattr(
            backup,
            "_restore_database_from_path",
            lambda _path: (_ for _ in ()).throw(OSError("database failed")),
        )

        with pytest.raises(OSError, match="database failed"):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_finishes_forward_when_database_swap_reports_after_commit(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"swap-committed"
        _, key = seed_model_with_blob(
            backup_env, name="Swap committed", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_restore = backup._restore_database_from_path

        def swap_then_raise(path: Path) -> None:
            real_restore(path)
            raise OSError("acknowledgement lost")

        monkeypatch.setattr(backup, "_restore_database_from_path", swap_then_raise)

        result = backup.restore_backup(meta.id)

        assert result["restored_files"] == 1
        assert Path(key).read_bytes() == content
        assert not (backup_env.backup_dir / f".restore-{meta.id}.journal").exists()

    def test_repeated_restore_replaces_stale_marker_before_new_swap(
        self, backup_env: BackupEnv
    ) -> None:
        content = b"repeatable-restore"
        _, key = seed_model_with_blob(
            backup_env, name="Repeatable restore", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)
        Path(key).unlink()
        result = backup.restore_backup(meta.id)

        assert result["restored_files"] == 1
        assert Path(key).read_bytes() == content
        with backup_env.new_session() as session:
            markers = session.exec(select(RestoreMarker)).all()
            assert len(markers) == 1
            assert markers[0].backup_id == meta.id

    def test_restore_keeps_maintenance_when_active_journal_ack_fails(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"journal-terminal"
        _, key = seed_model_with_blob(
            backup_env, name="Journal terminal", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def fail_active_ack(path: Path, event: dict[str, object]) -> None:
            if event.get("event") == "database_active":
                raise OSError("journal terminal failure")
            real_append(path, event)

        monkeypatch.setattr(backup, "_append_restore_journal", fail_active_ack)
        try:
            with pytest.raises(
                backup.RestoreConflictError,
                match="restore_post_swap_recovery_required",
            ):
                backup.restore_backup(meta.id)
            assert backup.restore_in_progress() is True
            assert Path(key).read_bytes() == content
            assert (backup_env.backup_dir / f".restore-{meta.id}.journal").exists()
        finally:
            backup._end_restore_maintenance()

    def test_restore_unknown_swap_state_preserves_bytes(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"unknown-swap-state"
        _, key = seed_model_with_blob(
            backup_env, name="Unknown swap state", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        monkeypatch.setattr(
            backup,
            "_restore_database_from_path",
            lambda _path: (_ for _ in ()).throw(OSError("swap uncertain")),
        )
        monkeypatch.setattr(
            backup, "_active_restore_marker", lambda _id, **_kwargs: None
        )

        try:
            with pytest.raises(
                backup.RestoreConflictError, match="restore_database_state_unknown"
            ):
                backup.restore_backup(meta.id)
            assert backup.restore_in_progress() is True
            assert Path(key).exists() is True
        finally:
            backup._end_restore_maintenance()

    def test_restore_preserves_final_destination_mutation(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Final Mutation", content=b"restore-content"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_apply = backup._apply_staged_blobs

        def mutate_after_apply(*args, **kwargs):
            result = real_apply(*args, **kwargs)
            Path(key).write_bytes(b"foreign-final-mutation")
            return result

        monkeypatch.setattr(backup, "_apply_staged_blobs", mutate_after_apply)

        with pytest.raises(
            backup.RestoreConflictError, match="restore_destination_changed"
        ):
            backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == b"foreign-final-mutation"

    def test_restore_reloads_the_latest_valid_publication_generation(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"generation-content"
        _, first_key = seed_model_with_blob(
            backup_env, name="Generation First", content=content
        )
        _, second_key = seed_model_with_blob(
            backup_env, name="Generation Second", content=b"generation-second"
        )
        meta = backup.create_backup()
        Path(first_key).unlink()
        Path(second_key).unlink()
        backend = get_backend()
        real_create = backend.create_stream
        first_receipts: list[storage_backend.CreationReceipt] = []

        def fail_second(source, destination: str):
            if destination == second_key:
                raise OSError("attempt one failure")
            receipt = real_create(source, destination)
            first_receipts.append(receipt)
            return receipt

        monkeypatch.setattr(backend, "create_stream", fail_second)
        with pytest.raises(OSError, match="attempt one failure"):
            backup.restore_backup(meta.id)

        def crash_second_attempt(source, destination: str):
            if destination == second_key:
                raise KeyboardInterrupt
            receipt = real_create(source, destination)
            first_receipts.append(receipt)
            return receipt

        monkeypatch.setattr(backend, "create_stream", crash_second_attempt)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        adopted_inode = Path(first_key).stat().st_ino

        backup.restore_backup(meta.id)

        assert len(first_receipts) == 2
        assert first_receipts[0].token != first_receipts[1].token
        assert Path(first_key).stat().st_ino == adopted_inode
        assert Path(first_key).read_bytes() == content

    def test_restore_resumes_an_intent_recorded_before_publish(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"before-publish"
        _, key = seed_model_with_blob(
            backup_env, name="Before Publish", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def crash_before_publish(source, destination: str):
            del source, destination
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", crash_before_publish)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param(_append_malformed_journal, id="malformed-json"),
            pytest.param(_append_non_object_event, id="non-object-event"),
            pytest.param(_replace_first_journal_event, id="invalid-first-event"),
            pytest.param(_append_unknown_journal_event, id="unknown-event"),
            pytest.param(_replace_generation_with_text, id="invalid-generation"),
            pytest.param(_skip_journal_generation, id="skipped-generation"),
            pytest.param(_append_duplicate_intent, id="duplicate-intent"),
            pytest.param(_publish_before_intent, id="published-before-intent"),
            pytest.param(_publish_wrong_generation, id="published-generation-mismatch"),
            pytest.param(_append_duplicate_publication, id="duplicate-publication"),
        ],
    )
    def test_restore_rejects_a_corrupt_journal_sequence(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        mutation: Callable[[Path, list[object], dict, str], None],
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Journal Invalid", content=b"journal-sequence"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def interrupt(_source, _destination: str):
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", interrupt)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        events: list[object] = [
            json.loads(line) for line in journal.read_text().splitlines()
        ]
        intent = events[1]
        assert isinstance(intent, dict)

        mutation(journal, events, intent, key)

        with pytest.raises(
            backup.RestoreConflictError, match="restore_journal_invalid"
        ):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    @pytest.mark.parametrize(
        ("mutation", "error"),
        [
            pytest.param(
                partial(
                    _replace_started_field,
                    field="archive_sha256",
                    value="0" * 64,
                ),
                "restore_journal_mismatch",
                id="archive",
            ),
            pytest.param(
                partial(_replace_started_field, field="backend", value="foreign"),
                "restore_journal_mismatch",
                id="backend",
            ),
            pytest.param(
                partial(
                    _replace_started_field,
                    field="namespaces",
                    value=["foreign:/vault"],
                ),
                "restore_journal_mismatch",
                id="namespace",
            ),
            pytest.param(
                partial(_replace_started_field, field="backup_id", value="foreign"),
                "restore_journal_mismatch",
                id="backup-id",
            ),
            pytest.param(
                _replace_intent_key, "restore_journal_invalid", id="unlisted-key"
            ),
            pytest.param(
                _increment_intent_size,
                "restore_journal_mismatch",
                id="intent-size",
            ),
            pytest.param(
                _replace_intent_hash,
                "restore_journal_mismatch",
                id="intent-hash",
            ),
            pytest.param(
                _replace_intent_namespace,
                "restore_journal_mismatch",
                id="intent-namespace",
            ),
        ],
    )
    def test_restore_rejects_a_mismatched_journal(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        mutation: Callable[[Path, list[object], dict, str], None],
        error: str,
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Journal Mismatch", content=b"journal-mismatch"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def interrupt(_source, _destination: str):
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", interrupt)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        events: list[object] = [
            json.loads(line) for line in journal.read_text().splitlines()
        ]
        intent = events[1]
        assert isinstance(intent, dict)
        mutation(journal, events, intent, key)

        with pytest.raises(backup.RestoreConflictError, match=error):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_rejects_another_active_journal(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Other Journal", content=b"other-journal"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        other = backup_env.backup_dir / ".restore-another.journal"
        other.write_text("reserved")

        with pytest.raises(
            backup.RestoreConflictError, match="restore_incomplete_other_backup"
        ):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    @pytest.mark.critical
    def test_restore_retracts_a_vanished_publication_generation(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"vanished-publication"
        _, key = seed_model_with_blob(
            backup_env, name="Vanished Publication", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def interrupt_after_publication(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "published":
                raise KeyboardInterrupt

        monkeypatch.setattr(
            backup, "_append_restore_journal", interrupt_after_publication
        )
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def interrupt_second_generation(_source, _destination: str):
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", interrupt_second_generation)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        durable_events = [json.loads(line) for line in journal.read_text().splitlines()]
        transitions = [
            (event.get("event"), event.get("generation"))
            for event in durable_events[-2:]
        ]
        assert transitions == [("retracted", 1), ("intent", 2)]

        monkeypatch.setattr(backend, "create_stream", real_create)
        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_successful_restore_removes_the_terminal_journal(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Journal Cleanup", content=b"journal-cleanup"
        )
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)

        assert not (backup_env.backup_dir / f".restore-{meta.id}.journal").exists()

    def test_successful_restore_preserves_backup_archive_ownership(
        self, backup_env: BackupEnv
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Archive Ownership", content=b"archive-ownership"
        )
        meta = backup.create_backup()
        Path(key).unlink()

        backup.restore_backup(meta.id)

        with backup_env.new_session() as session:
            ownership = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == str(meta.path)
                )
            ).one()
        assert ownership.object_kind == "backup"

    def test_restore_adopts_bytes_published_before_receipt_journaling(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"after-publish"
        _, key = seed_model_with_blob(backup_env, name="After Publish", content=content)
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def crash_after_publish(source, destination: str):
            real_create(source, destination)
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", crash_after_publish)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_reuses_a_journaled_publication(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"after-journal"
        _, key = seed_model_with_blob(backup_env, name="After Journal", content=content)
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def crash_after_journal(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "published":
                raise KeyboardInterrupt

        monkeypatch.setattr(backup, "_append_restore_journal", crash_after_journal)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    @pytest.mark.parametrize(
        ("mutation", "error"),
        [
            pytest.param(
                _invalidate_receipt_token,
                "restore_journal_invalid",
                id="invalid-receipt",
            ),
            pytest.param(
                _increment_receipt_size,
                "restore_journal_mismatch",
                id="receipt-mismatch",
            ),
        ],
    )
    def test_restore_rejects_invalid_published_receipt_evidence(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        mutation: Callable[[dict], None],
        error: str,
    ) -> None:
        content = b"published-evidence"
        _, key = seed_model_with_blob(
            backup_env, name="Published Invalid", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def interrupt_after_publication(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "published":
                raise KeyboardInterrupt

        monkeypatch.setattr(
            backup, "_append_restore_journal", interrupt_after_publication
        )
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)
        journal = backup_env.backup_dir / f".restore-{meta.id}.journal"
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        published = events[-1]
        assert published["event"] == "published"
        mutation(published)
        journal.write_text("".join(f"{json.dumps(event)}\n" for event in events))

        with pytest.raises(backup.RestoreConflictError, match=error):
            backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_reports_creation_match_failure(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"creation-match"
        _, key = seed_model_with_blob(
            backup_env, name="Creation Match", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        real_append = backup._append_restore_journal

        def interrupt_after_publication(path: Path, event: dict[str, object]) -> None:
            real_append(path, event)
            if event.get("event") == "published":
                raise KeyboardInterrupt

        monkeypatch.setattr(
            backup, "_append_restore_journal", interrupt_after_publication
        )
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backup, "_append_restore_journal", real_append)
        monkeypatch.setattr(
            get_backend(),
            "creation_matches",
            lambda _receipt: (_ for _ in ()).throw(OSError("match failed")),
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_destination_changed"
        ):
            backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_uses_guarded_adoption_fallback(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"guarded-adoption"
        _, key = seed_model_with_blob(
            backup_env, name="Guarded Adoption", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def interrupt_after_create(source, destination: str):
            real_create(source, destination)
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", interrupt_after_create)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        monkeypatch.setattr(
            backend,
            "adopt_existing",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(NotImplementedError),
        )

        backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    def test_restore_reports_adoption_failure(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = b"adoption-failure"
        _, key = seed_model_with_blob(
            backup_env, name="Adoption Failure", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def interrupt_after_create(source, destination: str):
            real_create(source, destination)
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", interrupt_after_create)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        monkeypatch.setattr(
            backend,
            "adopt_existing",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("adopt failed")),
        )

        with pytest.raises(
            backup.RestoreConflictError, match="restore_destination_changed"
        ):
            backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == content

    @pytest.mark.parametrize(
        ("fault", "error"),
        [
            pytest.param(
                _return_invalid_size_receipt,
                "restore_blob_size_mismatch",
                id="receipt-size",
            ),
            pytest.param(
                _publish_wrong_hash,
                "restore_blob_hash_mismatch",
                id="stored-hash",
            ),
            pytest.param(
                _publish_vanished_object,
                "restore_blob_hash_mismatch",
                id="vanished-object",
            ),
        ],
    )
    def test_restore_rejects_invalid_created_object_evidence(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        fault: Callable,
        error: str,
    ) -> None:
        content = b"created-evidence"
        _, key = seed_model_with_blob(
            backup_env, name="Created Invalid", content=content
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def invalid_create(source, destination: str):
            return fault(backend, real_create, content, source, destination)

        monkeypatch.setattr(backend, "create_stream", invalid_create)

        with pytest.raises(RuntimeError, match=error):
            backup.restore_backup(meta.id)

        assert not Path(key).exists()

    def test_restore_blocks_changed_bytes_from_an_interrupted_publish(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, key = seed_model_with_blob(
            backup_env, name="Changed Resume", content=b"restore-owned"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        backend = get_backend()
        real_create = backend.create_stream

        def crash_after_publish(source, destination: str):
            real_create(source, destination)
            raise KeyboardInterrupt

        monkeypatch.setattr(backend, "create_stream", crash_after_publish)
        with pytest.raises(KeyboardInterrupt):
            backup.restore_backup(meta.id)
        monkeypatch.setattr(backend, "create_stream", real_create)
        Path(key).write_bytes(b"foreign-replacement")

        with pytest.raises(
            backup.RestoreConflictError, match="restore_destination_changed"
        ):
            backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == b"foreign-replacement"

    def test_restore_skips_directory_entries_under_files_prefix(
        self,
        backup_env: BackupEnv,
    ):
        """A directory entry under files/ (tar.extractfile returns None for it)
        must be skipped, not crash the restore or count as a restored file."""
        import gzip
        import io
        import tarfile

        content = b"solid widget\n"
        _model_id, key = seed_model_with_blob(
            backup_env, name="Widget", content=content
        )
        meta = backup.create_backup()
        archive = Path(meta.path)

        # Rewrite the archive with an extra directory entry under files/.
        entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
        with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
            for member in tar.getmembers():
                data = tar.extractfile(member).read() if member.isfile() else None
                entries.append((member, data))

        updated_archive = io.BytesIO()
        with (
            gzip.GzipFile(fileobj=updated_archive, mode="wb") as gz,
            tarfile.open(fileobj=gz, mode="w:") as tar,
        ):
            for member, data in entries:
                if data is not None:
                    tar.addfile(member, io.BytesIO(data))
                else:
                    tar.addfile(member)
            dir_info = tarfile.TarInfo(name="files/empty-subdir")
            dir_info.type = tarfile.DIRTYPE
            tar.addfile(dir_info)
        from app.services.storage_ownership import replace_owned_bytes

        with backup_env.new_session() as session:
            replace_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(archive),
                updated_archive.getvalue(),
                object_kind="backup",
            )
            session.commit()

        Path(key).unlink()

        result = backup.restore_backup(meta.id)

        # Only the real blob counts as restored; the directory entry is skipped.
        assert result["restored_files"] == 1
        assert Path(key).read_bytes() == content

    def test_restore_skips_unreadable_files_member(self, backup_env: BackupEnv):
        """A files/ tar member that isn't a regular file (e.g. a directory entry)
        must be skipped during restore, not crash it or count toward
        restored_files."""
        import gzip
        import io
        import tarfile

        content = b"solid widget\n"
        _, key = seed_model_with_blob(backup_env, name="Widget", content=content)
        meta = backup.create_backup()
        archive = Path(meta.path)

        entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
        with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
            for member in tar.getmembers():
                data = tar.extractfile(member).read() if member.isfile() else None
                entries.append((member, data))

        updated_archive = io.BytesIO()
        with (
            gzip.GzipFile(fileobj=updated_archive, mode="wb") as gz,
            tarfile.open(fileobj=gz, mode="w:") as tar,
        ):
            for member, data in entries:
                if data is not None:
                    tar.addfile(member, io.BytesIO(data))
                else:
                    tar.addfile(member)
            dir_info = tarfile.TarInfo(name="files/subdir")
            dir_info.type = tarfile.DIRTYPE
            tar.addfile(dir_info)
        from app.services.storage_ownership import replace_owned_bytes

        with backup_env.new_session() as session:
            replace_owned_bytes(
                session,
                storage_backend.LocalStorageBackend(),
                str(archive),
                updated_archive.getvalue(),
                object_kind="backup",
            )
            session.commit()

        Path(key).unlink()

        result = backup.restore_backup(meta.id)

        assert result["restored_files"] == 1

    def test_restore_rejected_while_job_running(self, backup_env: BackupEnv):
        from app.services.jobs import registry

        model_id, _key = seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        job_id = registry.create()
        registry.update(job_id, state="running")
        try:
            with pytest.raises(backup.RestoreConflictError):
                backup.restore_backup(meta.id)
        finally:
            registry.update(job_id, state="completed")

        assert not backup.restore_in_progress()
        with backup_env.new_session() as session:
            assert session.get(Model, model_id) is not None

    def test_restore_maintenance_waits_for_admitted_mutation(
        self, backup_env: BackupEnv
    ):
        assert backup.begin_mutating_operation() is True
        maintenance_ready = threading.Event()
        release_maintenance = threading.Event()

        def enter_maintenance() -> None:
            backup._begin_restore_maintenance()
            maintenance_ready.set()
            release_maintenance.wait(timeout=5)
            backup._end_restore_maintenance()

        thread = threading.Thread(target=enter_maintenance)
        thread.start()
        try:
            for _ in range(100):
                if backup.restore_in_progress():
                    break
                time.sleep(0.01)
            assert backup.restore_in_progress() is True
            assert maintenance_ready.is_set() is False

            backup.end_mutating_operation()
            assert maintenance_ready.wait(timeout=5) is True
        finally:
            release_maintenance.set()
            thread.join(timeout=5)
            if backup.restore_in_progress():
                backup._end_restore_maintenance()

        assert backup._active_mutations == 0
        assert backup.restore_in_progress() is False

    def test_mutating_request_is_rejected_during_restore(
        self, client: TestClient, backup_env: BackupEnv
    ):
        headers = _auth_headers(backup_env)
        backup._restore_gate.set()
        try:
            response = client.post("/api/v1/backups", headers=headers)
        finally:
            backup._restore_gate.clear()

        assert response.status_code == 503
        assert response.json() == {"detail": "restore_in_progress"}

    def test_gc_skips_during_restore(self, backup_env: BackupEnv):
        from app.services import trash

        # A trashed row past retention would normally be purged.
        with backup_env.new_session() as session:
            from datetime import timedelta

            from app.core.time import utcnow
            from app.db.models import Tag

            session.add(
                Tag(
                    name="stale",
                    slug="stale",
                    deleted_at=utcnow() - timedelta(days=999),
                )
            )
            session.commit()

        backup._restore_gate.set()
        try:
            result = trash.gc_soft_deleted()
        finally:
            backup._restore_gate.clear()

        assert result == {"rows": 0, "orphan_blobs": 0}
        with backup_env.new_session() as session:
            assert session.exec(select(Tag)).first() is not None


class TestPurgeOldBackups:
    def test_retention_treats_unsupported_deletion_as_steady_state(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        sources = [
            backup.BackupMeta(
                id=identity,
                created_at="2020-01-01T00:00:00+00:00",
                size_bytes=1,
                storage_backend="local",
                file_count=0,
                app_version="0.13.0",
                path=identity,
                source_ref=identity,
            )
            for identity in ("unsupported", "deletable")
        ]
        attempted = []

        def delete(identity, *, source_ref):
            attempted.append(source_ref)
            if identity == "unsupported":
                raise backup.BackupDeleteUnsupportedError()
            return True

        monkeypatch.setattr(backup, "list_backup_sources", lambda: sources)
        monkeypatch.setattr(backup, "delete_backup", delete)
        assert backup.purge_old_backups(30) == 1
        assert backup.purge_old_backups(30) == 1
        assert attempted == ["unsupported", "deletable", "unsupported", "deletable"]
        assert "could not be removed" not in caplog.text

    def test_purge_old_backups_noop_when_retention_non_positive(
        self, backup_env: BackupEnv
    ):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        backup.create_backup()
        assert backup.purge_old_backups(retain_days=0) == 0
        assert backup.purge_old_backups(retain_days=-5) == 0

    def test_purge_old_backups_skips_entry_with_invalid_created_at(
        self,
        backup_env: BackupEnv,
    ):
        """An unowned archive is never listable or eligible for purge.

        The malformed timestamp is intentionally secondary: normal listing is
        ownership-only, so a hand-crafted archive must remain invisible until
        explicit adoption proves it safe.
        """
        import gzip
        import io
        import json
        import tarfile

        archive_path = (
            backup_env.backup_dir
            / "printstash-backup-20200101-000000-badc0ffeeb00.tar.gz"
        )
        manifest = {
            "version": backup.MANIFEST_VERSION,
            "created_at": "not-a-real-timestamp",
            "app_version": "0.0.0",
            "storage_backend": "local",
            "file_count": 0,
            "total_size_bytes": 0,
            "files": [],
        }
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        with gzip.open(archive_path, "wb") as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                info = tarfile.TarInfo(name="manifest.json")
                info.size = len(manifest_bytes)
                tar.addfile(info, io.BytesIO(manifest_bytes))
                db_data = b"fake"
                db_info = tarfile.TarInfo(name="db.sqlite3")
                db_info.size = len(db_data)
                tar.addfile(db_info, io.BytesIO(db_data))

        listed = {m.id for m in backup.list_backups()}
        assert "badc0ffeeb00" not in listed

        removed = backup.purge_old_backups(retain_days=30)

        assert removed == 0
        assert "badc0ffeeb00" not in {m.id for m in backup.list_backups()}

    def test_purge_keeps_fresh_removes_old(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        from datetime import timedelta

        from app.core.time import utcnow

        # An old backup: pin create_backup's clock 60 days into the past.
        monkeypatch.setattr(backup, "utcnow", lambda: utcnow() - timedelta(days=60))
        old = backup.create_backup()

        # A fresh backup at the real clock.
        monkeypatch.setattr(backup, "utcnow", utcnow)
        fresh = backup.create_backup()

        removed = backup.purge_old_backups(retain_days=30)

        assert removed == 1
        remaining = {m.id for m in backup.list_backups()}
        assert old.id not in remaining
        assert fresh.id in remaining

    def test_purge_continues_after_one_source_cannot_be_verified(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sources = [
            backup.BackupMeta(
                id="unverifiable",
                created_at="2020-01-01T00:00:00+00:00",
                size_bytes=1,
                storage_backend="s3",
                file_count=0,
                app_version="0.13.0",
                path="printstash-backups/unverifiable.tar.gz",
                location="s3",
                source_ref="first-source",
            ),
            backup.BackupMeta(
                id="deletable",
                created_at="2020-01-01T00:00:00+00:00",
                size_bytes=1,
                storage_backend="local",
                file_count=0,
                app_version="0.13.0",
                path="/tmp/deletable.tar.gz",
                source_ref="second-source",
            ),
        ]
        attempted: list[tuple[str, str | None]] = []

        def delete(source_id: str, *, source_ref: str | None = None) -> bool:
            attempted.append((source_id, source_ref))
            if source_id == "unverifiable":
                raise backup.BackupOwnershipError("provider credentials leaked")
            return True

        monkeypatch.setattr(backup, "list_backup_sources", lambda: sources)
        monkeypatch.setattr(backup, "delete_backup", delete)

        removed = backup.purge_old_backups(retain_days=30)

        assert removed == 1
        assert attempted == [
            ("unverifiable", "first-source"),
            ("deletable", "second-source"),
        ]


class TestDocument:
    def test_backup_includes_document_blobs(self, backup_env: BackupEnv):
        """Documents are vault-owned bytes: a backup that omits them is a lie."""
        content = b"%PDF-1.4 assembly manual\n"
        key = _seed_document_with_blob(backup_env, name="manual.pdf", content=content)
        meta = backup.create_backup()

        Path(key).unlink()
        result = backup.restore_backup(meta.id)

        assert Path(key).exists(), "document blob was never in the archive"
        assert Path(key).read_bytes() == content
        assert result["restored_files"] == 1

    def test_backup_includes_embedded_document_images(self, backup_env: BackupEnv):
        from app.services.storage_ownership import record_creation

        image_name = f"{'a' * 64}.png"
        with backup_env.new_session() as session:
            document = Document(name="Build notes", kind=DocumentKind.MARKDOWN)
            session.add(document)
            session.commit()
            session.refresh(document)
            document.body = (
                f"![diagram](/api/v1/documents/{document.id}/images/{image_name})"
            )
            key = get_backend().document_image_key(document.id, image_name)
            receipt = get_backend().create_bytes(b"irreplaceable-image", key)
            record_creation(session, receipt, object_kind="document_image")
            session.add(document)
            session.commit()

        meta = backup.create_backup()
        Path(key).unlink()

        result = backup.restore_backup(meta.id)

        assert Path(key).read_bytes() == b"irreplaceable-image"
        assert result["restored_files"] == 1


class TestStart:
    def test_a_restore_refused_for_a_running_job_is_audited(
        self,
        backup_env: BackupEnv,
    ):
        from app.db.models import AuditLog
        from app.services.jobs import registry

        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        job_id = registry.create()
        registry.update(job_id, state="running")
        try:
            with pytest.raises(backup.RestoreConflictError):
                backup.restore_backup(meta.id)
        finally:
            registry.update(job_id, state="completed")

        # No DB swap happened, so both rows survive in the current database.
        with backup_env.new_session() as session:
            actions = {row.action for row in session.exec(select(AuditLog)).all()}
        assert "restore.start" in actions
        assert "restore.failed" in actions


class TestRaises:
    def test_restore_cannot_bypass_an_unresolved_other_backup(
        self, backup_env: BackupEnv
    ) -> None:
        journal = backup_env.backup_dir / ".restore-active.journal"
        journal.write_text(
            json.dumps(
                {
                    "event": "started",
                    "version": 2,
                    "backup_id": "active-backup",
                    "archive_sha256": "a" * 64,
                    "operation_nonce": "b" * 64,
                    "backend": "local",
                    "namespaces": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        backup._restore_gate.set()
        try:
            with pytest.raises(
                backup.RestoreConflictError, match="restore_recovery_required"
            ):
                backup.restore_backup("different-backup")
        finally:
            backup._restore_gate.clear()
            journal.unlink()

    def test_restore_unknown_backup_raises(self, backup_env: BackupEnv):
        with pytest.raises(FileNotFoundError):
            backup.restore_backup("does-not-exist")

    def test_gate_is_cleared_when_restore_raises(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated failure mid-restore")

        monkeypatch.setattr(backup, "_download_backup_to_local", _boom)

        with pytest.raises(RuntimeError):
            backup.restore_backup(meta.id)

        assert not backup.restore_in_progress()

    def test_restore_of_corrupt_archive_raises_not_found(self, backup_env: BackupEnv):
        """A gzip/manifest-corrupt archive fails to even list (see
        ``test_list_backups_skips_archive_with_unreadable_manifest``), so
        ``get_backup`` can't find it and restore never reaches ``tarfile.open`` —
        it's rejected as unknown before any DB/file mutation, not mid-restore."""
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()
        Path(meta.path).write_bytes(Path(meta.path).read_bytes()[:20])

        with pytest.raises(FileNotFoundError):
            backup.restore_backup(meta.id)

        assert backup.restore_in_progress() is False


class TestDelete:
    def test_delete_unknown_backup_returns_false(self, backup_env: BackupEnv):
        assert backup.delete_backup("nope") is False


class TestFirst:
    def test_manifest_is_first_archive_member(self, backup_env: BackupEnv):
        """The manifest must be the first entry so listing (a streaming read) can
        stop after one small member instead of pulling the whole archive."""
        import gzip
        import tarfile

        seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
        seed_model_with_blob(backup_env, name="Gadget", content=b"solid gadget\n")
        meta = backup.create_backup()

        with gzip.open(Path(meta.path), "rb") as gz:
            with tarfile.open(fileobj=gz, mode="r|") as tar:
                first = next(iter(tar))
        assert first.name == "manifest.json"


class _BackupObjectStore:
    def __init__(
        self,
        payload: bytes,
        token: str,
        *,
        fail_get: Exception | None = None,
        fail_put: Exception | None = None,
        include_version: bool = True,
    ) -> None:
        self.payload = payload
        self.token = token
        self.fail_get = fail_get
        self.fail_put = fail_put
        self.include_version = include_version
        self.get_kwargs: dict[str, object] | None = None
        self.uploaded: bytes | None = None
        self.prefixes_seen: list[str] = []
        self.local_archive_dir: Path | None = None
        self.upload_observations: list[tuple[str, bool]] = []

    def head_object(self, **kwargs: object) -> dict[str, object]:
        response: dict[str, object] = {
            "ContentLength": len(self.payload),
            "Metadata": {"printstash-create-token": self.token},
            "ETag": '"archive-etag"',
        }
        if self.include_version:
            response["VersionId"] = str(kwargs.get("VersionId", "version-1"))
        return response

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_kwargs = kwargs
        if self.fail_get is not None:
            raise self.fail_get
        response: dict[str, object] = {
            "Body": io.BytesIO(self.payload),
            "ContentLength": len(self.payload),
            "ETag": '"archive-etag"',
        }
        if self.include_version:
            response["VersionId"] = str(kwargs.get("VersionId", "version-1"))
        return response

    def put_object(self, **kwargs: object) -> dict[str, str]:
        if self.fail_put is not None:
            raise self.fail_put
        body = kwargs["Body"]
        assert hasattr(body, "read")
        self.uploaded = body.read()
        self.payload = self.uploaded
        metadata = kwargs.get("Metadata")
        if isinstance(metadata, dict) and metadata.get("printstash-create-token"):
            self.token = str(metadata["printstash-create-token"])
        if self.local_archive_dir is not None:
            key = str(kwargs["Key"])
            archive = self.local_archive_dir / key.rsplit("/", 1)[-1]
            self.upload_observations.append((key, archive.is_file()))
        return {"ETag": '"uploaded-etag"'}

    def get_paginator(self, _name: str) -> "_BackupObjectStore":
        return self

    def paginate(self, **kwargs: object) -> list[dict[str, object]]:
        self.prefixes_seen.append(str(kwargs["Prefix"]))
        return []


class TestDiscoverUnownedS3Backups:
    def test_discovers_an_unowned_s3_archive_with_exact_identity(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 discovery", content=b"discovery")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._LEGACY_BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")

        def pages(**kwargs: object) -> list[dict[str, object]]:
            if kwargs["Prefix"] == backup._LEGACY_BACKUP_S3_PREFIX:
                return [{"Contents": [{"Key": key, "Size": len(payload)}]}]
            return [{"Contents": []}]

        store.paginate = pages  # type: ignore[method-assign]
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        candidates = backup.discover_unowned_s3_backups()

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["key"] == key
        assert candidate["backup_id"] == meta.id
        assert candidate["size_bytes"] == len(payload)
        assert candidate["namespace"] == (
            f"archive-bucket/{backup._LEGACY_BACKUP_S3_PREFIX}"
        )
        assert candidate["archive_sha256"] == hashlib.sha256(payload).hexdigest()
        assert candidate["source_ref"] == backup._source_ref(
            location="s3",
            namespace=f"archive-bucket/{backup._LEGACY_BACKUP_S3_PREFIX}",
            path=key,
            provider_ref=backup._backup_provider_ref(backup._backup_s3_config()),
        )
        assert store.get_kwargs is not None
        assert store.get_kwargs["VersionId"] == "version-1"

    def test_current_provider_candidate_is_not_hidden_by_foreign_provider_receipt(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(
            backup_env, name="S3 provider collision", content=b"collision"
        )
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")

        def pages(**kwargs: object) -> list[dict[str, object]]:
            if kwargs["Prefix"] == backup._BACKUP_S3_PREFIX:
                return [{"Contents": [{"Key": key, "Size": len(payload)}]}]
            return [{"Contents": []}]

        store.paginate = pages  # type: ignore[method-assign]
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        namespace = f"archive-bucket/{backup._BACKUP_S3_PREFIX}"
        with backup_env.new_session() as session:
            session.add(
                OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=key,
                    object_kind="backup",
                    state=StorageObjectState.COMMITTED,
                    token="foreign-token",
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    provider_ref="foreign-provider",
                    version_id="version-1",
                )
            )
            session.commit()

        candidates = backup.discover_unowned_s3_backups()

        assert [candidate["key"] for candidate in candidates] == [key]


class TestAdoptS3Backup:
    def _assert_no_remote_ledger_row(self, backup_env: BackupEnv, key: str) -> None:
        with backup_env.new_session() as session:
            assert (
                session.exec(
                    select(OwnedStorageObject).where(
                        OwnedStorageObject.backend == "backup-s3",
                        OwnedStorageObject.key == key,
                    )
                ).first()
                is None
            )

    def test_rejects_s3_adoption_when_source_ref_does_not_match_exact_locator(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 ref mismatch", content=b"adoption")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._LEGACY_BACKUP_S3_PREFIX}{Path(meta.path).name}"
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(
            backup, "_get_backup_s3", lambda: _BackupObjectStore(payload, "unused")
        )

        with pytest.raises(ValueError, match="backup_source_ref_mismatch"):
            backup.adopt_s3_backup(
                key,
                source_ref="wrong-source-ref",
                expected_archive_sha256=hashlib.sha256(payload).hexdigest(),
            )

        self._assert_no_remote_ledger_row(backup_env, key)

    def test_rejects_s3_adoption_when_expected_digest_does_not_match(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 digest mismatch", content=b"adoption")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        target = backup._get_backup_s3_target()
        assert target is not None
        source_ref = backup._source_ref(
            location="s3",
            namespace=f"archive-bucket/{backup._BACKUP_S3_PREFIX}",
            path=key,
            provider_ref=target.provider_ref,
        )

        with pytest.raises(RuntimeError, match="backup_archive_digest_mismatch"):
            backup.adopt_s3_backup(
                key,
                source_ref=source_ref,
                expected_archive_sha256="0" * 64,
            )

        self._assert_no_remote_ledger_row(backup_env, key)

    def test_rejects_s3_adoption_when_object_changes_between_proofs(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 replacement", content=b"adoption")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"

        class ReplacedObjectStore(_BackupObjectStore):
            def get_object(self, **kwargs: object) -> dict[str, object]:
                response = super().get_object(**kwargs)
                response["VersionId"] = "version-replaced"
                return response

        store = ReplacedObjectStore(payload, "unused")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        target = backup._get_backup_s3_target()
        assert target is not None
        source_ref = backup._source_ref(
            location="s3",
            namespace=f"archive-bucket/{backup._BACKUP_S3_PREFIX}",
            path=key,
            provider_ref=target.provider_ref,
        )

        with pytest.raises(RuntimeError, match="backup_remote_version_changed"):
            backup.adopt_s3_backup(
                key,
                source_ref=source_ref,
                expected_archive_sha256=hashlib.sha256(payload).hexdigest(),
            )

        self._assert_no_remote_ledger_row(backup_env, key)

    @pytest.mark.parametrize(
        "validation_error",
        [
            "backup_manifest_invalid",
            "backup_member_size_mismatch",
            "backup_member_hash_mismatch",
            "backup_manifest_invalid:db.sqlite3",
        ],
    )
    def test_invalid_archive_validation_never_creates_remote_ledger_row(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
        validation_error: str,
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 invalid archive", content=b"adoption")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setattr(
            backup,
            "_validate_archive_for_adoption",
            lambda _path: (_ for _ in ()).throw(RuntimeError(validation_error)),
        )
        target = backup._get_backup_s3_target()
        assert target is not None
        source_ref = backup._source_ref(
            location="s3",
            namespace=f"archive-bucket/{backup._BACKUP_S3_PREFIX}",
            path=key,
            provider_ref=target.provider_ref,
        )

        with pytest.raises(RuntimeError, match=validation_error.split(":", 1)[0]):
            backup.adopt_s3_backup(
                key,
                source_ref=source_ref,
                expected_archive_sha256=hashlib.sha256(payload).hexdigest(),
            )

        self._assert_no_remote_ledger_row(backup_env, key)

    def test_adopts_an_unowned_s3_archive_with_ledger_identity(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="S3 adoption", content=b"adoption")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        target = backup._get_backup_s3_target()
        assert target is not None
        source_ref = backup._source_ref(
            location="s3",
            namespace=f"archive-bucket/{backup._BACKUP_S3_PREFIX}",
            path=key,
            provider_ref=target.provider_ref,
        )
        digest = hashlib.sha256(payload).hexdigest()

        adopted = backup.adopt_s3_backup(
            key, source_ref=source_ref, expected_archive_sha256=digest
        )

        assert adopted.id == meta.id
        assert adopted.location == "s3"
        assert adopted.path == key
        assert adopted.archive_sha256 == digest
        assert adopted.source_ref == source_ref
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.backend == "backup-s3",
                    OwnedStorageObject.key == key,
                )
            ).one()
            assert row.state is StorageObjectState.COMMITTED
            assert row.namespace == f"archive-bucket/{backup._BACKUP_S3_PREFIX}"
            assert row.size_bytes == len(payload)
            assert row.sha256 == digest
            assert row.etag == '"archive-etag"'
            assert row.version_id == "version-1"
        assert store.get_kwargs is not None
        assert store.get_kwargs["VersionId"] == "version-1"

    def test_adopts_current_provider_without_rebinding_foreign_receipt(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(
            backup_env, name="S3 provider sibling", content=b"provider sibling"
        )
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        target = backup._get_backup_s3_target()
        assert target is not None
        namespace = f"archive-bucket/{backup._BACKUP_S3_PREFIX}"
        with backup_env.new_session() as session:
            session.add(
                OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=key,
                    object_kind="backup",
                    state=StorageObjectState.COMMITTED,
                    token="foreign-token",
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    provider_ref="foreign-provider",
                    version_id="foreign-version",
                )
            )
            session.commit()
        source_ref = backup._source_ref(
            location="s3",
            namespace=namespace,
            path=key,
            provider_ref=target.provider_ref,
        )
        digest = hashlib.sha256(payload).hexdigest()

        adopted = backup.adopt_s3_backup(
            key, source_ref=source_ref, expected_archive_sha256=digest
        )

        assert adopted.provider_ref == target.provider_ref
        with backup_env.new_session() as session:
            rows = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.backend == "backup-s3",
                    OwnedStorageObject.namespace == namespace,
                    OwnedStorageObject.key == key,
                )
            ).all()
            assert {row.provider_ref for row in rows} == {
                "foreign-provider",
                target.provider_ref,
            }
            current = next(
                row for row in rows if row.provider_ref == target.provider_ref
            )
            assert current.sha256 == digest
            assert current.etag == '"archive-etag"'
            assert current.version_id == "version-1"
            foreign = next(
                row for row in rows if row.provider_ref == "foreign-provider"
            )
            assert foreign.token == "foreign-token"
            assert foreign.version_id == "foreign-version"


class TestReconcileBackupPublications:
    def test_does_not_probe_a_pending_row_in_a_different_configured_bucket(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile old bucket", content=b"old")
        meta = backup.create_backup()
        calls: list[dict[str, object]] = []

        class RecordingStore(_BackupObjectStore):
            def head_object(self, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                return super().head_object(**kwargs)

        store = RecordingStore(Path(meta.path).read_bytes(), "old-token")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        monkeypatch.setattr(backup, "_backup_s3_last_signature", None)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "new-bucket")

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "old-bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-old-bucket.tar.gz"
            row.state = StorageObjectState.PENDING
            row.token = "old-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0
        assert calls == []

    def test_reconciles_a_local_archive_published_before_its_receipt(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile local", content=b"local")
        meta = backup.create_backup()

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.state = StorageObjectState.PENDING
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 1

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            assert row.state is StorageObjectState.COMMITTED
            assert row.last_error is None

    def test_reconciles_an_opendal_archive_with_matching_object_proof(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile OpenDAL", content=b"remote")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        namespace = "gdrive/PrintStash"
        key = "gdrive/PrintStash/printstash-backups/reconcile-opendal.tar.gz"
        provider_ref = "saved-profile"

        class Backend:
            def object_info(self, requested_key: str):
                assert requested_key == key
                return storage_backend.StorageObjectInfo(
                    size=len(payload), etag='"opendal-etag"', version_id="version-1"
                )

        class Destination:
            backend = Backend()

            def download_owned(
                self, owned: OwnedStorageObject, candidate: Path
            ) -> None:
                assert owned.state is StorageObjectState.COMMITTED
                candidate.write_bytes(payload)

        monkeypatch.setattr(
            backup, "destination_for_ownership", lambda _row: Destination()
        )
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-opendal-gdrive"
            row.namespace = namespace
            row.key = key
            row.provider_ref = provider_ref
            row.state = StorageObjectState.PENDING
            row.token = "opendal-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        reconciled = backup.reconcile_backup_publications()

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert reconciled == 1, (row.state, row.last_error)
            assert row.state is StorageObjectState.COMMITTED
            assert row.etag == '"opendal-etag"'
            assert row.version_id == "version-1"
            assert row.provider_ref == provider_ref

    def test_keeps_an_opendal_archive_pending_when_the_target_changes(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Changed OpenDAL", content=b"remote")
        meta = backup.create_backup()
        key = "gdrive/PrintStash/printstash-backups/changed-opendal.tar.gz"
        monkeypatch.setattr(backup, "destination_for_ownership", lambda _row: None)
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-opendal-gdrive"
            row.namespace = "gdrive/PrintStash"
            row.key = key
            row.provider_ref = "old-profile"
            row.state = StorageObjectState.PENDING
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.PENDING
            assert row.last_error == "retryable:backup_target_changed"

    def test_blocks_an_opendal_archive_with_mismatched_object_proof(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Mismatched OpenDAL", content=b"remote")
        meta = backup.create_backup()
        key = "gdrive/PrintStash/printstash-backups/mismatched-opendal.tar.gz"

        class Backend:
            def object_info(self, _key: str):
                return storage_backend.StorageObjectInfo(
                    size=Path(meta.path).stat().st_size + 1
                )

        destination = type("Destination", (), {"backend": Backend()})()
        monkeypatch.setattr(
            backup, "destination_for_ownership", lambda _row: destination
        )
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-opendal-gdrive"
            row.namespace = "gdrive/PrintStash"
            row.key = key
            row.provider_ref = "saved-profile"
            row.state = StorageObjectState.PENDING
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "RuntimeError"

    def test_blocks_a_pending_archive_from_an_unknown_backend(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Unknown backend", content=b"archive")
        meta = backup.create_backup()
        key = "printstash-backups/unknown-backend.tar.gz"
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "unknown-backup-backend"
            row.namespace = "unknown/backups"
            row.key = key
            row.state = StorageObjectState.PENDING
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "RuntimeError"

    def test_blocks_a_local_archive_without_publication_evidence(
        self, backup_env: BackupEnv
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile missing", content=b"missing")
        meta = backup.create_backup()

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.state = StorageObjectState.PENDING
            row.size_bytes = None
            row.sha256 = None
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "RuntimeError"

    def test_keeps_a_cloud_archive_pending_when_the_provider_is_unavailable(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: None)
        seed_model_with_blob(backup_env, name="Reconcile cloud", content=b"cloud")
        meta = backup.create_backup()

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "missing-bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-cloud.tar.gz"
            row.state = StorageObjectState.PENDING
            row.token = "token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key
                    == "printstash-backups/reconcile-cloud.tar.gz",
                )
            ).one()
            assert row.state is StorageObjectState.PENDING
            assert row.last_error == "retryable:backup_provider_unavailable"

    def test_reconciles_a_cloud_archive_with_matching_object_proof(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile remote", content=b"remote")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        store = _BackupObjectStore(payload, "reconcile-token")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-remote.tar.gz"
            target = backup._get_backup_s3_target()
            assert target is not None
            row.provider_ref = target.provider_ref
            row.state = StorageObjectState.PENDING
            row.token = "reconcile-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 1
        assert store.get_kwargs is not None
        assert store.get_kwargs["VersionId"] == "version-1"

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key
                    == "printstash-backups/reconcile-remote.tar.gz",
                )
            ).one()
            assert row.state is StorageObjectState.COMMITTED
            assert row.version_id == "version-1"
            assert row.etag == '"archive-etag"'

    def test_blocks_a_cloud_archive_with_mismatched_object_proof(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile mismatch", content=b"mismatch")
        meta = backup.create_backup()
        store = _BackupObjectStore(Path(meta.path).read_bytes(), "wrong-token")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-mismatch.tar.gz"
            target = backup._get_backup_s3_target()
            assert target is not None
            row.provider_ref = target.provider_ref
            row.state = StorageObjectState.PENDING
            row.token = "expected-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key
                    == "printstash-backups/reconcile-mismatch.tar.gz",
                )
            ).one()
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "RuntimeError"

    def test_blocks_a_cloud_archive_with_a_digest_mismatch(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile digest", content=b"digest")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        changed = bytes([payload[0] ^ 1]) + payload[1:]
        store = _BackupObjectStore(changed, "digest-token")
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-digest.tar.gz"
            target = backup._get_backup_s3_target()
            assert target is not None
            row.provider_ref = target.provider_ref
            row.state = StorageObjectState.PENDING
            row.token = "digest-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key
                    == "printstash-backups/reconcile-digest.tar.gz",
                )
            ).one()
            assert row.state is StorageObjectState.BLOCKED
            assert row.last_error == "RuntimeError"

    def test_retries_a_cloud_archive_when_download_fails(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Reconcile retry", content=b"retry")
        meta = backup.create_backup()
        store = _BackupObjectStore(
            Path(meta.path).read_bytes(), "retry-token", fail_get=OSError("offline")
        )
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = "bucket/printstash-backups"
            row.key = "printstash-backups/reconcile-retry.tar.gz"
            target = backup._get_backup_s3_target()
            assert target is not None
            row.provider_ref = target.provider_ref
            row.state = StorageObjectState.PENDING
            row.token = "retry-token"
            row.committed_at = None
            session.add(row)
            session.commit()

        assert backup.reconcile_backup_publications() == 0

        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key
                    == "printstash-backups/reconcile-retry.tar.gz",
                )
            ).one()
            assert row.state is StorageObjectState.PENDING
            assert row.last_error == "retryable:OSError"


class TestListS3Backups:
    def test_lists_a_committed_cloud_archive_from_both_prefixes(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_env, name="Cloud listing", content=b"listing")
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "unused")

        def pages(**kwargs: object) -> list[dict[str, object]]:
            store.prefixes_seen.append(str(kwargs["Prefix"]))
            if kwargs["Prefix"] == backup._BACKUP_S3_PREFIX:
                return [
                    {
                        "Contents": [
                            {"Key": key, "Size": len(payload)},
                            {
                                "Key": f"{backup._BACKUP_S3_PREFIX}unowned.tar.gz",
                                "Size": 1,
                            },
                        ]
                    }
                ]
            return [{"Contents": []}]

        store.paginate = pages  # type: ignore[method-assign]
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = (
                f"{backup.settings.backup_s3_bucket}/{backup._BACKUP_S3_PREFIX}"
            )
            row.key = key
            target = backup._get_backup_s3_target()
            assert target is not None
            row.provider_ref = target.provider_ref
            store.token = str(row.token)
            row.etag = '"archive-etag"'
            row.version_id = "version-1"
            session.add(row)
            session.commit()

        listed = backup._list_s3_backups()

        assert [item.id for item in listed] == [meta.id]
        assert listed[0].location == "s3"
        assert listed[0].size_bytes == len(payload)
        assert set(store.prefixes_seen) == {
            backup._BACKUP_S3_PREFIX,
            backup._LEGACY_BACKUP_S3_PREFIX,
        }

    def test_foreign_receipt_does_not_hide_current_provider_archive(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(
            backup_env, name="Cloud provider siblings", content=b"siblings"
        )
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"
        store = _BackupObjectStore(payload, "current-token")

        def pages(**kwargs: object) -> list[dict[str, object]]:
            if kwargs["Prefix"] == backup._BACKUP_S3_PREFIX:
                return [{"Contents": [{"Key": key, "Size": len(payload)}]}]
            return [{"Contents": []}]

        store.paginate = pages  # type: ignore[method-assign]
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        target = backup._get_backup_s3_target()
        assert target is not None
        namespace = f"archive-bucket/{backup._BACKUP_S3_PREFIX}"
        digest = hashlib.sha256(payload).hexdigest()
        with backup_env.new_session() as session:
            foreign = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            foreign.backend = "backup-s3"
            foreign.namespace = namespace
            foreign.key = key
            foreign.provider_ref = "foreign-provider"
            foreign.token = "foreign-token"
            foreign.etag = '"foreign-etag"'
            foreign.version_id = "foreign-version"
            session.add(foreign)
            session.add(
                OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=key,
                    object_kind="backup",
                    state=StorageObjectState.COMMITTED,
                    token="current-token",
                    size_bytes=len(payload),
                    sha256=digest,
                    provider_ref=target.provider_ref,
                    etag='"archive-etag"',
                    version_id="version-1",
                )
            )
            session.commit()

        listed = backup._list_s3_backups()

        assert [item.id for item in listed] == [meta.id]
        assert listed[0].provider_ref == target.provider_ref
        assert store.get_kwargs is not None
        assert store.get_kwargs["VersionId"] == "version-1"

    def test_listing_bounds_the_cloud_manifest_stream(
        self, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(
            backup_env,
            name="Cloud stream listing",
            content=os.urandom(2 * 1024 * 1024),
        )
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"{backup._BACKUP_S3_PREFIX}{Path(meta.path).name}"

        class TrackingBody(io.BytesIO):
            bytes_read = 0
            closed_by_consumer = False

            def read(self, size: int = -1) -> bytes:
                data = super().read(size)
                self.bytes_read += len(data)
                return data

            def close(self) -> None:
                self.closed_by_consumer = True
                super().close()

        class TrackingStore(_BackupObjectStore):
            body: TrackingBody | None = None

            def get_object(self, **kwargs: object) -> dict[str, object]:
                response = super().get_object(**kwargs)
                self.body = TrackingBody(self.payload)
                response["Body"] = self.body
                return response

        store = TrackingStore(payload, "listing-token")

        def pages(**kwargs: object) -> list[dict[str, object]]:
            if kwargs["Prefix"] == backup._BACKUP_S3_PREFIX:
                return [{"Contents": [{"Key": key, "Size": len(payload)}]}]
            return [{"Contents": []}]

        store.paginate = pages  # type: ignore[method-assign]
        monkeypatch.setattr(backup, "_get_backup_s3", lambda: store)
        monkeypatch.setitem(_overlay, "backup_s3_bucket", "archive-bucket")
        with backup_env.new_session() as session:
            row = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.object_kind == "backup",
                    OwnedStorageObject.key == meta.path,
                )
            ).one()
            row.backend = "backup-s3"
            row.namespace = f"archive-bucket/{backup._BACKUP_S3_PREFIX}"
            row.key = key
            row.token = "listing-token"
            row.etag = '"archive-etag"'
            row.version_id = "version-1"
            row.provider_ref = backup._backup_provider_ref(backup._backup_s3_config())
            store.token = row.token
            session.add(row)
            session.commit()

        listed = backup._list_s3_backups()

        assert [item.id for item in listed] == [meta.id]
        assert store.body is not None
        assert store.body.closed_by_consumer is True
        assert store.body.bytes_read < len(payload)
