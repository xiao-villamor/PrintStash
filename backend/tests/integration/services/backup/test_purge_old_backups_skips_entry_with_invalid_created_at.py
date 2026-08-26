"""Defends purge old backups skips entry with invalid created at at the services backup integration boundary.

A regression could make backup recovery delete or restore bytes without valid proof.
"""

from __future__ import annotations

from ._backup_shared import (
    BackupEnv,
    MagicMock,
    Path,
    TestClient,
    _auth_headers,
    _overlay,
    _read_model_names,
    _seed_model_with_blob,
    backup,
    pytest,
    requires_s3,
    sqlite3,
    storage_backend,
    tarfile,
)


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
    _, key = _seed_model_with_blob(backup_env, name="Widget", content=content)
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
    _model_id, key = _seed_model_with_blob(
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
    assert resp.json()["detail"] == "1 ingestion job(s) and 0 staging lease(s) active"


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


def test_end_mutating_operation_rejects_unbalanced_call() -> None:
    backup._active_mutations = 0

    with pytest.raises(RuntimeError, match="unbalanced_mutating_operation"):
        backup.end_mutating_operation()


def test_restore_maintenance_timeout_clears_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup._active_mutations = 1
    monkeypatch.setattr(backup, "_RESTORE_DRAIN_TIMEOUT_S", 0)

    with pytest.raises(backup.RestoreConflictError, match="still active"):
        backup._begin_restore_maintenance()

    assert backup.restore_in_progress() is False
    backup.end_mutating_operation()
    assert backup.begin_mutating_operation() is True
    backup.end_mutating_operation()


def test_validate_sqlite_snapshot_rejects_failed_integrity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("corrupt",)
    monkeypatch.setattr(backup.sqlite3, "connect", lambda _path: connection)

    with pytest.raises(RuntimeError, match="sqlite_snapshot_integrity_check_failed"):
        backup._validate_sqlite_snapshot(tmp_path / "snapshot.sqlite3")


def test_sqlite_snapshot_rejects_missing_database_file(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = backup_env.root / "missing.sqlite3"
    monkeypatch.setitem(_overlay, "db_url", f"sqlite:///{missing}")

    with pytest.raises(FileNotFoundError) as exc_info:
        with backup._sqlite_snapshot_file():
            pass

    assert exc_info.value.args == (missing,)


def test_backup_sqlite_copy_returns_integral_snapshot(backup_env: BackupEnv) -> None:
    payload = backup._backup_sqlite_copy()
    snapshot = backup_env.root / "copied.sqlite3"
    snapshot.write_bytes(payload)

    with sqlite3.connect(snapshot) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()

    assert result == ("ok",)


def test_local_backup_ownership_requires_current_proof(backup_env: BackupEnv) -> None:
    archive = backup_env.backup_dir / "printstash-backup-unowned.tar.gz"
    archive.write_bytes(b"unowned")
    meta = backup.BackupMeta(
        id="unowned",
        created_at="2026-01-01T00:00:00+00:00",
        size_bytes=archive.stat().st_size,
        storage_backend="local",
        file_count=0,
        app_version="0.0.0",
        path=str(archive),
    )

    with pytest.raises(
        backup.BackupOwnershipError, match="backup_storage_ownership_unverified"
    ):
        backup._require_backup_archive_owned(meta)


def test_cloud_backup_ownership_requires_configured_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = backup.BackupMeta(
        id="cloud",
        created_at="2026-01-01T00:00:00+00:00",
        size_bytes=1,
        storage_backend="s3",
        file_count=0,
        app_version="0.0.0",
        path="printstash-backups/cloud.tar.gz",
        location="s3",
    )
    monkeypatch.setattr(backup, "_get_backup_s3", lambda: None)

    with pytest.raises(
        backup.BackupOwnershipError, match="backup_storage_ownership_unverified"
    ):
        backup._require_backup_archive_owned(meta)


def test_stage_restore_rejects_unsafe_member_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    source = tmp_path / "payload"
    source.write_bytes(b"escape")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="../escape")

    with pytest.raises(RuntimeError, match="backup_manifest_invalid"):
        backup._stage_restore_archive(archive, tmp_path / "staging")

    assert not (tmp_path.parent / "escape").exists()


def test_stage_restore_requires_database_member(tmp_path: Path) -> None:
    archive = tmp_path / "missing-db.tar.gz"
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"files": []}')
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(manifest, arcname="manifest.json")

    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(RuntimeError, match="backup_member_missing:db.sqlite3"):
        backup._stage_restore_archive(archive, staging)
