"""Defends gc skips during restore at the services backup integration boundary.

A regression could make backup recovery delete or restore bytes without valid proof.
"""

from __future__ import annotations

from ._backup_shared import (
    BackupEnv,
    Path,
    Session,
    TestClient,
    _auth_headers,
    _seed_model_with_blob,
    backup,
    pytest,
    select,
    storage_backend,
    tarfile,
    threading,
    time,
)


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


def test_delete_backup_aborts_on_permission_error(backup_env: BackupEnv):
    _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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
