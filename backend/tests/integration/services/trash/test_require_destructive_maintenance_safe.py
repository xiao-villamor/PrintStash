"""Defends require destructive maintenance safe at the services trash integration boundary.

A regression could permanently delete bytes that are still owned or shared.
"""

from __future__ import annotations

from ._trash_shared import (
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    ObjectIdentity,
    Path,
    Session,
    ShareLink,
    StorageDeleteIntent,
    StorageCapabilities,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    _binary_document,
    _cleanup_orphan_blobs,
    _model_with_file,
    _overlay,
    _owned_write,
    _write,
    gc_soft_deleted,
    get_backend,
    hashlib,
    pytest,
    select,
    timedelta,
    trash,
    utcnow,
)


def test_scheduled_gc_skips_storage_backed_guarded_purge(
    db_session: Session, storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _model_with_file(db_session, storage, "guarded-scheduled")
    model = db_session.get(Model, artifact.model_id)
    assert model is not None
    model.deleted_at = utcnow() - timedelta(days=2)
    db_session.commit()
    monkeypatch.setattr(
        storage,
        "_capabilities",
        StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        ),
    )

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] == 0
    assert result["resources_blocked"] == 1
    assert storage.exists(artifact.path)


class TestRequireDestructiveMaintenanceSafe:
    def test_open_namespace_escape_blocks_destructive_maintenance(
        self, db_session: Session
    ) -> None:
        user = User(username="audit-owner", hashed_password="not-used")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        run = VaultAuditRun(requested_by=user.id, mode=VaultAuditMode.QUICK)
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="managed_storage_namespace_escape",
                severity="critical",
                state=VaultAuditFindingState.OPEN,
                resource_type="storage",
                resource_identifier="vault",
                details_json='{"detail":"managed root escaped"}',
            )
        )
        db_session.commit()

        with pytest.raises(
            trash.UnsafeStorageDeleteError, match="storage_cleanup_blocked"
        ):
            trash._require_destructive_maintenance_safe(db_session)


class TestClaimPurge:
    def test_unpersisted_resource_is_rejected(self, db_session: Session) -> None:
        model = Model(name="unpersisted", slug="unpersisted", hash="u" * 64)

        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash._claim_purge(db_session, model)


class TestPreflightPrimaryKeys:
    def test_access_probe_failure_is_translated(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = get_backend()

        def reject(_keys):
            raise PermissionError("read-only")

        monkeypatch.setattr(backend, "verify_destructive_access", reject)

        with pytest.raises(
            trash.UnsafeStorageDeleteError, match="storage_delete_access_unverified"
        ):
            trash._preflight_primary_keys(db_session, ["owned-key"])


class TestRestoreModel:
    def test_active_purge_claim_is_rejected(self, db_session: Session) -> None:
        model = Model(
            name="claimed",
            slug="claimed-restore",
            hash="r" * 64,
            deleted_at=utcnow(),
            purge_token="claim-token",
        )
        db_session.add(model)
        db_session.commit()

        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.restore_model(db_session, model)


class TestRestoreDocument:
    def test_active_purge_claim_is_rejected(self, db_session: Session) -> None:
        document = Document(
            name="claimed-document",
            kind=DocumentKind.MARKDOWN,
            deleted_at=utcnow(),
            purge_token="claim-token",
        )
        db_session.add(document)
        db_session.commit()

        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.restore_document(db_session, document)


class TestHardDeleteExpiredModels:
    def test_negative_retention_preserves_expired_models(
        self, db_session: Session
    ) -> None:
        model = Model(
            name="retained",
            slug="negative-retention",
            hash="n" * 64,
            deleted_at=utcnow() - timedelta(days=365),
        )
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)

        purged_ids = trash.hard_delete_expired_models(db_session, retention_days=-1)

        assert purged_ids == []
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None


def test_gc_preserves_document_blobs(db_session: Session, storage) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists(), "GC deleted a live document's blob"


def test_gc_preserves_model_blobs(db_session: Session, storage) -> None:
    f = _model_with_file(db_session, storage, "widget")

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_file_derivatives_while_artifact_exists(
    db_session: Session, storage
) -> None:
    artifact = _model_with_file(db_session, storage, "derived-live")
    keys = (
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys:
        _write(key)

    _cleanup_orphan_blobs(db_session)

    assert all(Path(key).exists() for key in keys)


def test_gc_preserves_trashed_model_blobs(db_session: Session, storage) -> None:
    """A trashed model's bytes must survive until hard delete — restore needs them."""
    f = _model_with_file(db_session, storage, "trashed")
    f.deleted_at = utcnow()
    db_session.add(f)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_document_blobs_when_models_exist(
    db_session: Session, storage
) -> None:
    """The two censuses must union, not shadow each other."""
    f = _model_with_file(db_session, storage, "widget")
    doc = _binary_document(db_session, storage)

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()
    assert Path(storage.document_file_key(doc.id, doc.filename)).exists()


def test_gc_preserves_unclaimed_files_in_local_storage(
    db_session: Session, storage
) -> None:
    """A configured local root may already contain user-managed files.

    Absence from PrintStash's database is not proof that PrintStash owns a file,
    even when its path resembles the vault layout.  Scheduled maintenance must
    therefore never discover and delete local files by walking ``data_dir``.
    """
    existing_library_file = _write(
        str(
            Path(storage.blob_key("gone", 1, "gone.stl")).parents[2]
            / "My Library"
            / "part.stl"
        )
    )
    printstash_shaped_file = _write(storage.blob_key("gone", 1, "gone.stl"))
    document_shaped_file = _write(storage.document_file_key(999, "gone.pdf"))

    removed = _cleanup_orphan_blobs(db_session)

    assert Path(existing_library_file).exists()
    assert Path(printstash_shaped_file).exists()
    assert Path(document_shaped_file).exists()
    assert removed == 0


def test_gc_does_not_guess_ownership_after_document_row_is_missing(
    db_session: Session, storage
) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)
    db_session.delete(doc)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists()


def test_gc_ignores_markdown_documents(db_session: Session, storage) -> None:
    """Markdown docs own no blob — they must not contribute a bogus key."""
    doc = Document(name="notes", kind=DocumentKind.MARKDOWN, body="# hi")
    db_session.add(doc)
    db_session.commit()

    assert _cleanup_orphan_blobs(db_session) == 0


def test_gc_hard_deletes_expired_document_and_its_blob(
    db_session: Session, storage
) -> None:
    doc = _binary_document(db_session, storage)
    document_id = doc.id
    key = storage.document_file_key(doc.id, doc.filename)
    doc.deleted_at = utcnow()
    db_session.add(doc)
    db_session.commit()

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] >= 1
    assert not Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Document, document_id) is None


def test_gc_hard_deletes_expired_artifact_and_its_derivatives(
    db_session: Session, storage
) -> None:
    artifact = _model_with_file(db_session, storage, "derived-expired")
    artifact_id = artifact.id
    keys = (
        artifact.path,
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys[1:]:
        _owned_write(db_session, storage, key)
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()

    gc_soft_deleted(retention_days=0)

    assert all(not Path(key).exists() for key in keys)
    db_session.expire_all()
    assert db_session.get(File, artifact_id) is None


def test_negative_retention_disables_gc_and_preserves_owned_bytes(
    db_session: Session, storage
) -> None:
    artifact = _model_with_file(db_session, storage, "retention-disabled")
    artifact.deleted_at = utcnow() - timedelta(days=365)
    db_session.add(artifact)
    db_session.commit()

    result = gc_soft_deleted(retention_days=-1)

    assert result == {"rows": 0, "orphan_blobs": 0}
    assert Path(artifact.path).exists()
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None


def test_hard_delete_aborts_when_owned_storage_is_suddenly_unmounted(
    db_session: Session, storage, tmp_path: Path
) -> None:
    artifact = _model_with_file(db_session, storage, "unmounted")
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()
    mounted_root = Path(_overlay["data_dir"])
    detached_root = tmp_path / "detached-files"
    mounted_root.rename(detached_root)
    mounted_root.mkdir()

    result = gc_soft_deleted(retention_days=0)

    assert result["resources_blocked"] == 1
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None
    assert (detached_root / "unmounted" / "v1" / "unmounted.stl").exists()


def test_hard_delete_aborts_on_read_only_storage_and_preserves_row(
    db_session: Session,
    storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _model_with_file(db_session, storage, "readonly")
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()
    import tempfile

    original_mkstemp = tempfile.mkstemp

    def denied(*args, **kwargs):
        if Path(kwargs.get("dir", "")) == Path(artifact.path).parent:
            raise PermissionError("read-only mount")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr("app.services.storage_backend.tempfile.mkstemp", denied)
    result = gc_soft_deleted(retention_days=0)

    assert result["resources_blocked"] == 1
    db_session.expire_all()
    assert db_session.get(File, artifact.id) is not None
    assert Path(artifact.path).exists()


def test_gc_skips_legacy_candidate_without_blocking_verifiable_candidates(
    db_session: Session, storage
) -> None:
    first = _model_with_file(db_session, storage, "owned-first")
    first.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(first)

    legacy_model = Model(name="Legacy", slug="legacy", hash="legacy-hash")
    db_session.add(legacy_model)
    db_session.commit()
    db_session.refresh(legacy_model)
    legacy_path = storage.blob_key("legacy", 1, "legacy.stl")
    _write(legacy_path, b"legacy-user-bytes")
    legacy = File(
        model_id=legacy_model.id,
        path=legacy_path,
        original_filename="legacy.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=17,
        sha256="legacy-file-hash",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)
    first_id = first.id
    first_path = first.path

    result = gc_soft_deleted(retention_days=0)

    db_session.expire_all()
    assert result["resources_blocked"] == 1
    assert not Path(first_path).exists()
    assert Path(legacy_path).read_bytes() == b"legacy-user-bytes"
    assert db_session.get(File, first_id) is None
    assert db_session.get(File, legacy.id) is not None


def test_gc_adopts_and_purges_pre_ledger_artifact_with_matching_content(
    db_session: Session, storage
) -> None:
    model = Model(name="Legacy owned", slug="legacy-owned", hash="legacy-owned-hash")
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    content = b"artifact created before the ownership ledger"
    legacy_path = storage.blob_key("legacy-owned", 1, "legacy.stl")
    _write(legacy_path, content)
    artifact = File(
        model_id=model.id,
        path=legacy_path,
        original_filename="legacy.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    model.deleted_at = utcnow() - timedelta(days=1)
    db_session.add_all([model, artifact])
    db_session.commit()
    model_id = model.id
    artifact_id = artifact.id

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] == 1
    assert result["storage_completed"] == 1
    assert not Path(legacy_path).exists()
    db_session.expire_all()
    assert db_session.get(Model, model_id) is None
    assert db_session.get(File, artifact_id) is None


def test_hard_delete_late_storage_failure_leaks_remainder_without_db_rollback(
    db_session: Session, storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.trash import hard_delete_model

    first = _model_with_file(db_session, storage, "two-file-purge")
    model = db_session.get(Model, first.model_id)
    assert model is not None
    second_key = _owned_write(
        db_session,
        storage,
        storage.blob_key("two-file-purge", 2, "second.stl"),
        b"second",
    )
    second = File(
        model_id=model.id,
        path=second_key,
        original_filename="second.stl",
        file_type=FileType.STL,
        version=2,
        size_bytes=6,
        sha256="second-file-hash",
    )
    db_session.add(second)
    db_session.commit()
    db_session.refresh(second)

    real_rollback = storage.rollback_create
    deletes = 0

    def fail_second(receipt):
        nonlocal deletes
        deletes += 1
        if deletes == 2:
            raise OSError("storage became unavailable")
        return real_rollback(receipt)

    monkeypatch.setattr(storage, "rollback_create", fail_second)

    hard_delete_model(db_session, model)
    assert deletes == 0, "storage deletion must never happen before SQL commit"
    db_session.commit()

    from app.services.storage_deletion import process_storage_delete_intents

    result = process_storage_delete_intents()

    assert not Path(first.path).exists()
    assert Path(second_key).read_bytes() == b"second"
    assert result.completed == 1
    assert result.pending == 1
    db_session.expire_all()
    assert db_session.get(Model, model.id) is None
    assert db_session.get(File, first.id) is None
    assert db_session.get(File, second.id) is None


def test_hard_delete_rollback_preserves_blob_and_discards_intent(
    db_session: Session, storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.trash import hard_delete_model

    artifact = _model_with_file(db_session, storage, "rollback-safe")
    artifact_key = artifact.path
    model = db_session.get(Model, artifact.model_id)
    assert model is not None
    calls = 0

    def _unexpected_delete(_receipt):
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(storage, "rollback_create", _unexpected_delete)

    hard_delete_model(db_session, model)
    assert calls == 0
    assert db_session.exec(select(StorageDeleteIntent)).all()
    db_session.rollback()

    assert calls == 0
    assert Path(artifact.path).exists()
    assert db_session.get(Model, model.id) is not None
    assert (
        db_session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.resource_kind == "file",
                StorageDeleteIntent.key == artifact_key,
            )
        ).all()
        == []
    )


def test_hard_delete_resolves_share_link_before_model_delete(
    db_session: Session, storage
) -> None:
    from app.services.storage_deletion import process_storage_delete_intents
    from app.services.trash import hard_delete_model

    artifact = _model_with_file(db_session, storage, "shared-purge")
    model = db_session.get(Model, artifact.model_id)
    assert model is not None
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.add(
        ShareLink(
            model_id=model.id,
            token_hash="f" * 64,
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    db_session.commit()

    hard_delete_model(db_session, model)
    assert Path(artifact.path).exists()
    db_session.commit()
    process_storage_delete_intents()

    assert db_session.get(Model, model.id) is None
    assert not Path(artifact.path).exists()
