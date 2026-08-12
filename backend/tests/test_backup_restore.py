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

import hashlib
import os
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

import app.services.backup as backup
import app.services.storage_backend as storage_backend
from app.core.config import _overlay
from app.db.models import Document, DocumentKind, File, FileType, Model, User
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    override_session_factory,
)
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import get_backend


@dataclass
class BackupEnv:
    root: Path
    data_dir: Path
    backup_dir: Path
    db_file: Path
    engine: object

    def new_session(self) -> Session:
        return Session(self.engine)


@pytest.fixture
def backup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[BackupEnv]:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    backup_dir = tmp_path / "backups"
    db_dir = tmp_path / "db"
    for d in (data_dir, thumb_dir, backup_dir, db_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "vault.sqlite"
    db_url = f"sqlite:///{db_file}"

    # Point the effective config at our file-based vault.
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": data_dir,
            "thumb_dir": thumb_dir,
            "backup_dir": backup_dir,
            "db_url": db_url,
        }
    )

    # A real on-disk SQLite DB that the session factory and the backup
    # service's file-level reads/writes both target.
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))

    # Reset cached singletons so they pick up our overlay.
    monkeypatch.setattr(storage_backend, "_backend", None, raising=False)
    monkeypatch.setattr(backup, "_backup_s3", None, raising=False)
    # Real restores wait a grace period for in-flight jobs to finish; tests
    # don't need to pay that wall-clock cost.
    monkeypatch.setattr(backup, "_RESTORE_GRACE_PERIOD_S", 0)

    try:
        yield BackupEnv(
            root=tmp_path,
            data_dir=data_dir,
            backup_dir=backup_dir,
            db_file=db_file,
            engine=engine,
        )
    finally:
        engine.dispose()


def _seed_model_with_blob(
    env: BackupEnv, *, name: str, content: bytes
) -> tuple[int, str]:
    """Create a Model + File row and write the blob through the backend.

    Returns ``(model_id, storage_key)``.
    """
    slug = name.lower().replace(" ", "-")
    key = get_backend().blob_key(slug, 1, f"{slug}.stl")
    get_backend().write_bytes(content, key)

    sha = hashlib.sha256(content).hexdigest()
    with env.new_session() as session:
        model = Model(name=name, slug=slug, hash=sha)
        session.add(model)
        session.commit()
        session.refresh(model)
        f = File(
            model_id=model.id,
            path=key,
            original_filename=f"{slug}.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=len(content),
            sha256=sha,
        )
        session.add(f)
        session.commit()
        return model.id, key


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
        user = User(
            username="backup-admin",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_backup_archive_contents(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
    _seed_model_with_blob(backup_env, name="Gadget", content=b"solid gadget\n")

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


def test_create_backup_includes_rows_committed_only_to_wal(backup_env: BackupEnv):
    """A raw copy of the main SQLite file omits committed WAL pages."""
    with backup_env.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    _seed_model_with_blob(
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


def test_verify_backup_checks_manifest_members_and_sizes(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Verified", content=b"solid verified\n")
    meta = backup.create_backup()

    result = backup.verify_backup(meta.id)

    assert result.valid is True
    assert result.app_compatible is True
    assert result.checked_members == 3


def test_create_backup_fails_when_owned_blob_is_missing(backup_env: BackupEnv):
    _, key = _seed_model_with_blob(backup_env, name="Missing", content=b"gone")
    get_backend().delete(key)

    with pytest.raises(FileNotFoundError):
        backup.create_backup()


def test_backup_excludes_user_owned_external_artifacts(backup_env: BackupEnv):
    external = backup_env.root / "nas" / "linked.stl"
    external.parent.mkdir()
    external.write_bytes(b"user-owned")
    with backup_env.new_session() as session:
        model = Model(name="Linked", slug="linked", hash="c" * 64)
        session.add(model)
        session.commit()
        session.add(
            File(
                model_id=model.id,
                path=str(external),
                original_filename=external.name,
                file_type=FileType.STL,
                is_external=True,
                size_bytes=external.stat().st_size,
                sha256="d" * 64,
            )
        )
        session.commit()

    meta = backup.create_backup()

    assert meta.file_count == 0


def test_manifest_is_first_archive_member(backup_env: BackupEnv):
    """The manifest must be the first entry so listing (a streaming read) can
    stop after one small member instead of pulling the whole archive."""
    import gzip
    import tarfile

    _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
    _seed_model_with_blob(backup_env, name="Gadget", content=b"solid gadget\n")
    meta = backup.create_backup()

    with gzip.open(Path(meta.path), "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            first = next(iter(tar))
    assert first.name == "manifest.json"


def test_backup_appears_in_list_and_get(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    listed = backup.list_backups()
    assert any(m.id == meta.id for m in listed)

    fetched = backup.get_backup(meta.id)
    assert fetched is not None
    assert fetched.id == meta.id
    assert fetched.file_count == 1


def test_download_backup_archive_endpoint(client: TestClient, backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    resp = client.get(
        f"/api/v1/backups/{meta.id}/download",
        headers=_auth_headers(backup_env),
    )

    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"\x1f\x8b")
    assert Path(meta.path).name in resp.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Restore round trip
# ---------------------------------------------------------------------------


def test_restore_recovers_database_rows(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()

    # Disaster: wipe every model row.
    with backup_env.new_session() as session:
        for m in session.exec(select(Model)).all():
            session.delete(m)
        session.commit()
    backup_env.engine.dispose()

    assert _read_model_names(backup_env) == []

    backup.restore_backup(meta.id)

    assert "Widget" in _read_model_names(backup_env)


def test_restore_replaces_live_wal_state_without_replay(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
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

        backup.restore_backup(meta.id)

        assert live.execute("SELECT name FROM models").fetchone() == ("Widget",)
        assert _read_model_names(backup_env) == ["Widget"]
    finally:
        live.close()


def test_restore_recovers_blob_bytes(backup_env: BackupEnv):
    content = b"solid widget\nendsolid\n"
    _model_id, key = _seed_model_with_blob(backup_env, name="Widget", content=content)
    meta = backup.create_backup()

    # Disaster: delete the stored blob.
    Path(key).unlink()
    assert not Path(key).exists()

    result = backup.restore_backup(meta.id)

    assert result["restored_files"] == 1
    # The blob the database references must be back, byte-for-byte.
    assert Path(key).exists(), "restored blob is missing at its storage key"
    assert Path(key).read_bytes() == content


def test_backup_includes_document_blobs(backup_env: BackupEnv):
    """Documents are vault-owned bytes: a backup that omits them is a lie."""
    content = b"%PDF-1.4 assembly manual\n"
    key = _seed_document_with_blob(backup_env, name="manual.pdf", content=content)
    meta = backup.create_backup()

    Path(key).unlink()
    result = backup.restore_backup(meta.id)

    assert Path(key).exists(), "document blob was never in the archive"
    assert Path(key).read_bytes() == content
    assert result["restored_files"] == 1


def test_download_then_restore_endpoint_round_trip(
    client: TestClient, backup_env: BackupEnv
):
    content = b"solid endpoint widget\nendsolid\n"
    _model_id, key = _seed_model_with_blob(
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


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_restore_unknown_backup_raises(backup_env: BackupEnv):
    with pytest.raises(FileNotFoundError):
        backup.restore_backup("does-not-exist")


# ---------------------------------------------------------------------------
# Audit trail (0.8.5 item 3): backup/restore mutate the filesystem and swap
# the DB file, so they don't flow through the ORM after_flush hook — the
# service writes AuditLog rows explicitly.
# ---------------------------------------------------------------------------


def test_backup_writes_audit_row(backup_env: BackupEnv):
    from app.db.models import AuditLog

    backup.create_backup()

    with backup_env.new_session() as session:
        rows = session.exec(
            select(AuditLog).where(AuditLog.action == "backup.create")
        ).all()
    assert len(rows) == 1
    assert rows[0].resource_type == "backup"


def test_restore_writes_complete_row_on_success(backup_env: BackupEnv):
    from app.db.models import AuditLog

    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

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


def test_restore_rejected_while_job_running_writes_start_and_failed_rows(
    backup_env: BackupEnv,
):
    from app.db.models import AuditLog
    from app.services.jobs import registry

    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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


def test_failed_restore_writes_failed_row(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import AuditLog

    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-restore")

    monkeypatch.setattr(backup, "_download_backup_to_local", _boom)

    with pytest.raises(RuntimeError):
        backup.restore_backup(meta.id)

    with backup_env.new_session() as session:
        actions = {row.action for row in session.exec(select(AuditLog)).all()}
    assert "restore.failed" in actions


def test_failed_blob_restore_rolls_back_files_and_keeps_database(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    _, first_key = _seed_model_with_blob(
        backup_env, name="First", content=b"backup-first"
    )
    _, second_key = _seed_model_with_blob(
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

    real_write = backup._write_staged_blob
    writes = 0

    def fail_second_write(staged_path: Path, key: str) -> int:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated second blob failure")
        return real_write(staged_path, key)

    monkeypatch.setattr(backup, "_write_staged_blob", fail_second_write)

    with pytest.raises(OSError, match="second blob failure"):
        backup.restore_backup(meta.id)

    assert Path(first_key).read_bytes() == b"current-first"
    assert Path(second_key).read_bytes() == b"current-second"
    assert "Current First" in _read_model_names(backup_env)


# ---------------------------------------------------------------------------
# Restore gate (item 11: quiesce background loops during restore)
# ---------------------------------------------------------------------------


def test_restore_rejected_while_job_running(backup_env: BackupEnv):
    from app.services.jobs import registry

    model_id, _key = _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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


def test_gc_skips_during_restore(backup_env: BackupEnv):
    from app.services import trash

    # A trashed row past retention would normally be purged.
    with backup_env.new_session() as session:
        from datetime import timedelta

        from app.core.time import utcnow
        from app.db.models import Tag

        session.add(
            Tag(name="stale", slug="stale", deleted_at=utcnow() - timedelta(days=999))
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


def test_restore_maintenance_waits_for_admitted_mutation(backup_env: BackupEnv):
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
    client: TestClient, backup_env: BackupEnv
):
    headers = _auth_headers(backup_env)
    backup._restore_gate.set()
    try:
        response = client.post("/api/v1/backups", headers=headers)
    finally:
        backup._restore_gate.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "restore_in_progress"}


def test_gate_is_cleared_when_restore_raises(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-restore")

    monkeypatch.setattr(backup, "_download_backup_to_local", _boom)

    with pytest.raises(RuntimeError):
        backup.restore_backup(meta.id)

    assert not backup.restore_in_progress()


def test_delete_backup_removes_archive(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    assert Path(meta.path).exists()

    assert backup.delete_backup(meta.id) is True
    assert not Path(meta.path).exists()
    assert backup.get_backup(meta.id) is None


def test_delete_unknown_backup_returns_false(backup_env: BackupEnv):
    assert backup.delete_backup("nope") is False


def test_backup_id_round_trips_despite_timestamped_name(backup_env: BackupEnv):
    """The archive name embeds a hyphenated timestamp before the id; the id
    derived on list/get must still equal the one create_backup returned
    (regression for the rsplit-based id extraction)."""
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    # id is the trailing 12-hex token, not a timestamp fragment.
    assert len(meta.id) == 12
    assert all(c in "0123456789abcdef" for c in meta.id)
    assert f"-{meta.id}.tar.gz" in Path(meta.path).name

    fetched = backup.get_backup(meta.id)
    assert fetched is not None and fetched.id == meta.id


def test_purge_keeps_fresh_removes_old(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
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


# ---------------------------------------------------------------------------
# Corrupted / invalid archives (local — no S3 endpoint needed)
# ---------------------------------------------------------------------------


def test_list_backups_skips_archive_with_unreadable_manifest(
    backup_env: BackupEnv, caplog: pytest.LogCaptureFixture
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    good = backup.create_backup()

    corrupt_path = (
        backup_env.backup_dir / "printstash-backup-20200101-000000-deadbeefcafe.tar.gz"
    )
    corrupt_path.write_bytes(b"not a gzip file at all")

    listed = {m.id for m in backup.list_backups()}
    assert listed == {good.id}  # the corrupt archive is skipped, not raised


def test_restore_of_corrupt_archive_raises_not_found(backup_env: BackupEnv):
    """A gzip/manifest-corrupt archive fails to even list (see
    ``test_list_backups_skips_archive_with_unreadable_manifest``), so
    ``get_backup`` can't find it and restore never reaches ``tarfile.open`` —
    it's rejected as unknown before any DB/file mutation, not mid-restore."""
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    Path(meta.path).write_bytes(Path(meta.path).read_bytes()[:20])

    with pytest.raises(FileNotFoundError):
        backup.restore_backup(meta.id)

    assert backup.restore_in_progress() is False


def test_get_backup_archive_path_raises_for_unknown_id(backup_env: BackupEnv):
    with pytest.raises(FileNotFoundError):
        backup.get_backup_archive_path("does-not-exist")


def test_backup_sqlite_copy_raises_for_non_file_db(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(backup, "_db_path", lambda: None)
    with pytest.raises(RuntimeError, match="not a file-based SQLite database"):
        backup._backup_sqlite_copy()


def test_restore_database_raises_for_non_file_db(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(backup, "_db_path", lambda: None)
    with pytest.raises(RuntimeError, match="cannot restore to non-file database"):
        backup._restore_database(b"irrelevant")


def test_find_blobs_fails_for_unreadable_owned_blob(backup_env: BackupEnv):
    _model_id, key = _seed_model_with_blob(
        backup_env, name="Widget", content=b"solid widget\n"
    )
    Path(key).unlink()

    with pytest.raises(FileNotFoundError):
        backup._find_blobs()


def test_create_backup_fails_if_blob_vanishes_mid_write(
    backup_env: BackupEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """A backup never reports success after a censused blob vanishes."""
    _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")

    def _boom(*args, **kwargs):
        raise OSError("vanished")

    monkeypatch.setattr(backup, "_add_file_to_tar", _boom)

    with caplog.at_level("ERROR"), pytest.raises(OSError, match="vanished"):
        backup.create_backup()

    assert any(
        "failed while streaming owned blobs" in r.message for r in caplog.records
    )
    assert list(backup_env.backup_dir.glob("*.tar.gz")) == []


def test_list_local_backups_empty_when_dir_missing(backup_env: BackupEnv):
    import shutil

    shutil.rmtree(backup_env.backup_dir)
    assert backup._list_local_backups() == []
    assert backup.list_backups() == []


def test_delete_backup_logs_but_survives_permission_error(
    backup_env: BackupEnv, caplog: pytest.LogCaptureFixture
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    # Removing a file requires write on its parent dir, not the file itself.
    backup_env.backup_dir.chmod(0o500)
    try:
        with caplog.at_level("ERROR"):
            result = backup.delete_backup(meta.id)
    finally:
        backup_env.backup_dir.chmod(0o700)

    assert result is False
    assert any("failed to delete local" in r.message for r in caplog.records)
    assert Path(meta.path).exists()

    # Cleanup so tmp_path teardown can remove the archive.
    backup.delete_backup(meta.id)


def test_download_backup_to_local_raises_when_local_file_missing(
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


def test_has_member_false_for_missing_entry(backup_env: BackupEnv):
    import tarfile

    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()

    with tarfile.open(Path(meta.path), mode="r:gz") as tar:
        assert backup._has_member(tar, "manifest.json") is True
        assert backup._has_member(tar, "nonexistent.entry") is False


def test_restore_key_map_empty_for_legacy_archive_without_manifest(tmp_path: Path):
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


def test_restore_key_map_empty_for_corrupt_manifest_json(tmp_path: Path):
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


def test_purge_old_backups_noop_when_retention_non_positive(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    backup.create_backup()
    assert backup.purge_old_backups(retain_days=0) == 0
    assert backup.purge_old_backups(retain_days=-5) == 0


def test_backup_s3_key_prefixes_archive_name():
    assert (
        backup._backup_s3_key("printstash-backup-20240101-000000-abc123.tar.gz")
        == "printstash-backups/printstash-backup-20240101-000000-abc123.tar.gz"
    )


def test_create_backup_raises_when_blob_size_changes_mid_archive(
    backup_env: BackupEnv,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """If the recorded size for a censused blob no longer matches what actually
    gets streamed into the tar, the archive must not be reported as complete."""
    _, key = _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")

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


def test_list_backups_merges_s3_only_entry_and_local_wins_on_id_collision(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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


def test_read_manifest_returns_none_when_manifest_entry_is_a_directory(tmp_path: Path):
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


def test_read_manifest_returns_none_when_no_manifest_member_present(tmp_path: Path):
    import gzip
    import io
    import tarfile

    gz_archive = tmp_path / "no-manifest.tar.gz"
    with gzip.open(gz_archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        data = b"fake db bytes"
        info = tarfile.TarInfo(name="db.sqlite3")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    assert backup._read_manifest(gz_archive) is None


def test_restore_key_map_empty_when_manifest_entry_is_a_directory(tmp_path: Path):
    import tarfile

    archive = tmp_path / "dir-manifest.tar"
    with tarfile.open(archive, mode="w") as tar:
        info = tarfile.TarInfo(name="manifest.json")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)

    with tarfile.open(archive, mode="r") as tar:
        assert backup._restore_key_map(tar) == {}


def test_restore_skips_directory_entries_under_files_prefix(
    backup_env: BackupEnv,
):
    """A directory entry under files/ (tar.extractfile returns None for it)
    must be skipped, not crash the restore or count as a restored file."""
    import gzip
    import io
    import tarfile

    content = b"solid widget\n"
    _model_id, key = _seed_model_with_blob(backup_env, name="Widget", content=content)
    meta = backup.create_backup()
    archive = Path(meta.path)

    # Rewrite the archive with an extra directory entry under files/.
    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member).read() if member.isfile() else None
            entries.append((member, data))

    with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w:") as tar:
        for member, data in entries:
            if data is not None:
                tar.addfile(member, io.BytesIO(data))
            else:
                tar.addfile(member)
        dir_info = tarfile.TarInfo(name="files/empty-subdir")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)

    Path(key).unlink()

    result = backup.restore_backup(meta.id)

    # Only the real blob counts as restored; the directory entry is skipped.
    assert result["restored_files"] == 1
    assert Path(key).read_bytes() == content


def test_purge_old_backups_skips_entry_with_invalid_created_at(
    backup_env: BackupEnv,
):
    """A backup whose manifest has a non-ISO ``created_at`` (hand-crafted or
    from some future format change) must be skipped, not crash the purge."""
    import gzip
    import io
    import json
    import tarfile

    archive_path = (
        backup_env.backup_dir / "printstash-backup-20200101-000000-badc0ffeeb00.tar.gz"
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


# ---------------------------------------------------------------------------
# Narrow branch closures: S3 key helper, size-mismatch abort, list_backups
# merge/dedup, and the "member present but unreadable" edge cases in
# _read_manifest / _restore_key_map / restore_backup (all local-only, no
# S3 endpoint needed).
# ---------------------------------------------------------------------------


def test_list_backups_merges_s3_only_entry_and_local_wins_on_dup_id(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    """Exercises the merge/dedup loop in list_backups() without a real S3
    endpoint: _list_s3_backups() is stubbed to return a cloud-only entry plus
    a duplicate of a local id, and the loop's own logic is what's checked."""
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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


def test_read_manifest_returns_none_when_manifest_member_unreadable(tmp_path: Path):
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


def test_read_manifest_returns_none_when_manifest_member_absent(tmp_path: Path):
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


def test_restore_key_map_empty_when_manifest_member_unreadable(tmp_path: Path):
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


def test_restore_skips_unreadable_files_member(backup_env: BackupEnv):
    """A files/ tar member that isn't a regular file (e.g. a directory entry)
    must be skipped during restore, not crash it or count toward
    restored_files."""
    import gzip
    import io
    import tarfile

    content = b"solid widget\n"
    _seed_model_with_blob(backup_env, name="Widget", content=content)
    meta = backup.create_backup()
    archive = Path(meta.path)

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member).read() if member.isfile() else None
            entries.append((member, data))

    with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w:") as tar:
        for member, data in entries:
            if data is not None:
                tar.addfile(member, io.BytesIO(data))
            else:
                tar.addfile(member)
        dir_info = tarfile.TarInfo(name="files/subdir")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)

    result = backup.restore_backup(meta.id)

    assert result["restored_files"] == 1


# ---------------------------------------------------------------------------
# S3 backup destination (independent from vault storage) — needs a real endpoint
# ---------------------------------------------------------------------------

_S3_ENDPOINT = os.environ.get("PRINTSTASH_TEST_S3_ENDPOINT")
requires_s3 = pytest.mark.skipif(
    not _S3_ENDPOINT, reason="set PRINTSTASH_TEST_S3_ENDPOINT to run S3 backup tests"
)


@pytest.fixture
def backup_s3_env(backup_env: BackupEnv) -> Iterator[BackupEnv]:
    import uuid as _uuid

    bucket = f"printstash-backup-test-{_uuid.uuid4().hex[:12]}"
    _overlay.update(
        {
            "backup_s3_bucket": bucket,
            "backup_s3_endpoint_url": _S3_ENDPOINT,
            "backup_s3_region": "us-east-1",
            "backup_s3_access_key": os.environ.get(
                "PRINTSTASH_TEST_S3_ACCESS_KEY", "printstash"
            ),
            "backup_s3_secret_key": os.environ.get(
                "PRINTSTASH_TEST_S3_SECRET_KEY", "printstash-secret"
            ),
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


@requires_s3
def test_create_backup_uploads_to_s3(backup_s3_env: BackupEnv):
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")

    meta = backup.create_backup()

    s3 = backup._get_backup_s3()
    key = backup._backup_s3_key(Path(meta.path).name)
    head = s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
    assert head["ContentLength"] == meta.size_bytes


@requires_s3
def test_list_backups_finds_s3_only_backup(backup_s3_env: BackupEnv):
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()

    # Simulate cloud-only: the local copy is gone, only the S3 upload remains.
    Path(meta.path).unlink()

    found = backup.get_backup(meta.id)
    assert found is not None
    assert found.location == "s3"
    assert found.file_count == meta.file_count


@requires_s3
def test_restore_downloads_s3_only_backup_before_restoring(backup_s3_env: BackupEnv):
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()
    Path(meta.path).unlink()

    result = backup.restore_backup(meta.id)

    assert result["backup_id"] == meta.id
    assert _read_model_names(backup_s3_env) == ["Widget"]
    # _download_backup_to_local must have pulled a fresh local copy.
    assert Path(meta.path).exists()


# ---------------------------------------------------------------------------
# Router branches (404/409/500) — local backend, no S3 endpoint
# ---------------------------------------------------------------------------


def test_list_backups_endpoint(client: TestClient, backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    headers = _auth_headers(backup_env)

    resp = client.get("/api/v1/backups", headers=headers)

    assert resp.status_code == 200, resp.text
    ids = {row["backup_id"] for row in resp.json()}
    assert meta.id in ids


def test_database_backup_capability_reports_sqlite_support(
    client: TestClient, backup_env: BackupEnv
):
    headers = _auth_headers(backup_env)

    response = client.get(
        "/api/v1/backups/capabilities/database",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "database_backend": "sqlite",
        "create_supported": True,
        "restore_supported": True,
    }


def test_postgres_backup_capability_fails_before_creating_or_restoring(
    client: TestClient,
    backup_env: BackupEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import _overlay

    monkeypatch.setitem(
        _overlay,
        "db_url",
        "postgresql://printstash:secret@database/printstash",
    )
    headers = _auth_headers(backup_env)

    capability = client.get(
        "/api/v1/backups/capabilities/database",
        headers=headers,
    )
    create = client.post("/api/v1/backups", headers=headers)
    restore = client.post("/api/v1/backups/any-id/restore", headers=headers)

    assert capability.status_code == 200
    assert capability.json() == {
        "database_backend": "postgresql",
        "create_supported": False,
        "restore_supported": False,
    }
    assert create.status_code == 501
    assert create.json()["detail"] == "database_backup_not_supported"
    assert restore.status_code == 501
    assert restore.json()["detail"] == "database_backup_not_supported"
    assert list(backup_env.backup_dir.glob("*.tar.gz")) == []


def test_get_backup_endpoint_returns_metadata(
    client: TestClient, backup_env: BackupEnv
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    headers = _auth_headers(backup_env)

    resp = client.get(f"/api/v1/backups/{meta.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backup_id"] == meta.id
    assert body["file_count"] == meta.file_count
    assert body["location"] == "local"


def test_get_backup_endpoint_not_found(client: TestClient, backup_env: BackupEnv):
    headers = _auth_headers(backup_env)
    resp = client.get("/api/v1/backups/does-not-exist", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "backup_not_found"


def test_download_backup_endpoint_not_found(client: TestClient, backup_env: BackupEnv):
    headers = _auth_headers(backup_env)
    resp = client.get("/api/v1/backups/does-not-exist/download", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "backup_not_found"


def test_download_backup_endpoint_500_on_unexpected_error(
    client: TestClient, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    def _boom(_backup_id: str):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(backup, "get_backup_archive_path", _boom)
    headers = _auth_headers(backup_env)

    resp = client.get("/api/v1/backups/whatever/download", headers=headers)

    assert resp.status_code == 500
    assert "disk on fire" in resp.json()["detail"]


def test_delete_backup_endpoint_removes_archive(
    client: TestClient, backup_env: BackupEnv
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    headers = _auth_headers(backup_env)

    resp = client.delete(f"/api/v1/backups/{meta.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"backup_id": meta.id, "deleted": True}
    assert not Path(meta.path).exists()


def test_delete_backup_endpoint_not_found(client: TestClient, backup_env: BackupEnv):
    headers = _auth_headers(backup_env)
    resp = client.delete("/api/v1/backups/does-not-exist", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "backup_not_found"


def test_restore_backup_endpoint_not_found(client: TestClient, backup_env: BackupEnv):
    headers = _auth_headers(backup_env)
    resp = client.post("/api/v1/backups/does-not-exist/restore", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "backup_not_found"


def test_restore_backup_endpoint_conflict_while_job_running(
    client: TestClient, backup_env: BackupEnv
):
    from app.services.jobs import registry

    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    headers = _auth_headers(backup_env)

    job_id = registry.create()
    registry.update(job_id, state="running")
    try:
        resp = client.post(f"/api/v1/backups/{meta.id}/restore", headers=headers)
    finally:
        registry.update(job_id, state="completed")

    assert resp.status_code == 409
    assert "retry" in resp.json()["detail"]


def test_restore_backup_endpoint_500_on_unexpected_error(
    client: TestClient, backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
    meta = backup.create_backup()
    headers = _auth_headers(backup_env)

    def _boom(_backup_id: str):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(backup, "restore_backup", _boom)

    resp = client.post(f"/api/v1/backups/{meta.id}/restore", headers=headers)

    assert resp.status_code == 500
    assert "kaboom" in resp.json()["detail"]


@requires_s3
def test_delete_backup_removes_s3_copy(backup_s3_env: BackupEnv):
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()

    s3 = backup._get_backup_s3()
    key = backup._backup_s3_key(Path(meta.path).name)
    assert s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)

    assert backup.delete_backup(meta.id) is True

    import botocore.exceptions

    with pytest.raises(botocore.exceptions.ClientError):
        s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
