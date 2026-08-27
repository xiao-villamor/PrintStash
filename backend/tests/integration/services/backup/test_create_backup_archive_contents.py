"""Defends create backup archive contents at the services backup integration boundary.

A regression could make backup recovery delete or restore bytes without valid proof.
"""

from __future__ import annotations

from ._backup_shared import (
    SENTINEL_FILE_HASH,
    SENTINEL_MODEL_HASH,
    BackupEnv,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    OwnedStorageObject,
    Path,
    Printer,
    PrintJob,
    PrintJobState,
    TestClient,
    _auth_headers,
    _read_model_names,
    _seed_document_with_blob,
    _seed_model_with_blob,
    backup,
    get_backend,
    json,
    pytest,
    select,
    sqlite3,
    storage_backend,
    tarfile,
)


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
    direct = get_backend().direct_path(key)
    assert direct is not None
    direct.unlink()  # Simulate loss outside PrintStash; unchecked delete is disabled.

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


def test_backup_ignores_external_job_sentinel_but_keeps_vault_artifact(
    backup_env: BackupEnv,
):
    """The /dev/null placeholder is not a blob, while real vault files remain."""
    _model_id, vault_key = _seed_model_with_blob(
        backup_env, name="Vault artifact", content=b"real vault bytes"
    )
    with backup_env.new_session() as session:
        sentinel_model = Model(
            name="__external__",
            slug="__external__",
            hash=SENTINEL_MODEL_HASH,
        )
        session.add(sentinel_model)
        session.commit()
        session.refresh(sentinel_model)
        sentinel_file = File(
            model_id=sentinel_model.id,
            path="/dev/null",
            original_filename="__external__",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=0,
            sha256=SENTINEL_FILE_HASH,
        )
        session.add(sentinel_file)
        printer = Printer(name="External history")
        session.add(printer)
        session.commit()
        session.refresh(sentinel_file)
        session.refresh(printer)
        session.add(
            PrintJob(
                printer_id=printer.id,
                file_id=sentinel_file.id,
                model_id=sentinel_model.id,
                remote_filename="external.gcode",
                source="external",
                state=PrintJobState.COMPLETED,
            )
        )
        session.commit()

    meta = backup.create_backup()

    assert meta.file_count == 1
    with tarfile.open(meta.path, mode="r:gz") as archive:
        manifest = archive.extractfile("manifest.json")
        assert manifest is not None
        entries = json.loads(manifest.read())["files"]
    assert [entry["key"] for entry in entries] == [vault_key]


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


def test_restore_recovers_database_rows(backup_env: BackupEnv):
    _, key = _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
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


def test_restore_replaces_live_wal_state_without_replay(backup_env: BackupEnv):
    _, key = _seed_model_with_blob(backup_env, name="Widget", content=b"solid widget\n")
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


def test_backup_includes_embedded_document_images(backup_env: BackupEnv):
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


def test_restore_unknown_backup_raises(backup_env: BackupEnv):
    with pytest.raises(FileNotFoundError):
        backup.restore_backup("does-not-exist")


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

    _, key = _seed_model_with_blob(backup_env, name="Widget", content=b"x")
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


def test_restore_collision_preserves_files_and_database(backup_env: BackupEnv):
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

    with pytest.raises(backup.RestoreConflictError, match="destination_exists"):
        backup.restore_backup(meta.id)

    assert Path(first_key).read_bytes() == b"current-first"
    assert Path(second_key).read_bytes() == b"current-second"
    assert "Current First" in _read_model_names(backup_env)


def test_failed_blob_restore_removes_only_receipted_partial_creates(
    backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
):
    _, first_key = _seed_model_with_blob(
        backup_env, name="First", content=b"backup-first"
    )
    _, second_key = _seed_model_with_blob(
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
