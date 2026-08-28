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

import json
import sqlite3
import tarfile
import threading
import time
from pathlib import Path
from typing import Iterator

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
    FileType,
    Model,
    OwnedStorageObject,
    PrintJobState,
)
from app.services.auth import create_access_token
from app.services.storage_backend import get_backend
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.factories import (
    build_file,
    build_model,
    build_print_job,
    build_printer,
    build_user,
)
from tests.integration._backup_harness import BackupEnv, seed_model_with_blob


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


class TestListLocalBackups:
    def test_list_local_backups_empty_when_dir_missing(self, backup_env: BackupEnv):
        import shutil

        shutil.rmtree(backup_env.backup_dir)
        assert backup._list_local_backups() == []
        assert backup.list_backups() == []


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
        # Same id as the local backup but different metadata: local must win.
        s3_dupe = backup.BackupMeta(
            id=local_meta.id,
            created_at=local_meta.created_at,
            size_bytes=999999,
            storage_backend="s3",
            file_count=999,
            app_version="0.0.0",
            path="printstash-backups/dupe.tar.gz",
            location="s3",
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


class TestDeleteBackup:
    def test_delete_backup_removes_archive(self, backup_env: BackupEnv):
        seed_model_with_blob(backup_env, name="Widget", content=b"x")
        meta = backup.create_backup()
        assert Path(meta.path).exists()

        assert backup.delete_backup(meta.id) is True
        assert not Path(meta.path).exists()
        assert backup.get_backup(meta.id) is None

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

    @requires_s3
    def test_delete_backup_removes_s3_copy(self, backup_s3_env: BackupEnv):
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup.create_backup()

        s3 = backup._get_backup_s3()
        key = backup._backup_s3_key(Path(meta.path).name)
        assert s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)

        assert backup.delete_backup(meta.id) is True

        import botocore.exceptions

        with pytest.raises(botocore.exceptions.ClientError):
            s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)


class TestDownloadBackupToLocal:
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
        # _download_backup_to_local must have pulled a fresh local copy.
        assert Path(meta.path).exists()


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


class TestRestoreDatabase:
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
        """A backup whose manifest has a non-ISO ``created_at`` (hand-crafted or
        from some future format change) must be skipped, not crash the purge."""
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
        assert "badc0ffeeb00" in listed

        removed = backup.purge_old_backups(retain_days=30)

        assert removed == 0
        assert "badc0ffeeb00" in {m.id for m in backup.list_backups()}

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
