"""Defends expired orphan cover lease removes stale ownership proof at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._source_covers_lifecycle_shared import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    CreationReceipt,
    InboxItem,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    Session,
    SQLiteSessionFactory,
    SQLModel,
    StagingLease,
    StorageObjectInfo,
    User,
    _backend,
    _png,
    _receipt,
    _set_sqlite_pragmas,
    _source,
    create_engine,
    event,
    hashlib,
    inbox,
    json,
    process_source_cover_upload,
    pytest,
    record_creation,
    select,
    source_covers,
    staging_leases,
    uuid,
)


def test_expired_orphan_cover_lease_removes_stale_ownership_proof(
    db_session: Session,
) -> None:
    source = _source(db_session)
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/orphan.webp",
        size_bytes=4,
    )
    db_session.add(cover)
    db_session.flush()
    lease = staging_leases.create_cover_lease(
        db_session,
        model_source_cover_id=cover.id or 0,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=4,
        sha256="a" * 64,
    )
    record_creation(
        db_session,
        _receipt(key=cover.storage_key, token="stale"),
        object_kind="model_source_cover",
    )
    cover_id = cover.id
    db_session.commit()

    connection = db_session.connection()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.exec_driver_sql(
        "DELETE FROM model_source_covers WHERE id = ?", (cover_id,)
    )
    db_session.commit()
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.expire_all()

    assert source_covers.expire_pending(db_session, _backend(), lease=lease) is True
    assert db_session.get(StagingLease, lease.id) is None
    assert db_session.exec(select(OwnedStorageObject)).all() == []


def test_restart_reconcile_discards_unpublished_cover_intent(
    db_session: Session,
) -> None:
    source = _source(db_session)
    db_session.commit()
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/restart-missing.webp",
        size_bytes=4,
    )
    db_session.add(cover)
    db_session.flush()
    lease = staging_leases.create_cover_lease(
        db_session,
        model_source_cover_id=cover.id or 0,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=4,
        sha256="d" * 64,
    )
    db_session.commit()
    backend = _backend()
    backend.adopt_existing.side_effect = NotImplementedError
    backend.object_info.return_value = None

    assert source_covers.reconcile_pending(db_session, backend) == 1
    db_session.commit()
    assert db_session.get(ModelSourceCover, cover.id) is None
    assert db_session.get(StagingLease, lease.id) is None


def test_restart_reconcile_continues_past_uncertain_cover_intent(
    db_session: Session,
) -> None:
    sources = [_source(db_session), _source(db_session)]
    keys = ["opaque/covers/uncertain.webp", "opaque/covers/absent.webp"]
    covers: list[ModelSourceCover] = []
    leases: list[StagingLease] = []
    for source, key in zip(sources, keys, strict=True):
        cover = ModelSourceCover(
            provenance_source_id=source.id, storage_key=key, size_bytes=4
        )
        db_session.add(cover)
        db_session.flush()
        covers.append(cover)
        leases.append(
            staging_leases.create_cover_lease(
                db_session,
                model_source_cover_id=cover.id or 0,
                owner_user_id=None,
                destination_key=key,
                size_bytes=4,
                sha256="b" * 64,
            )
        )
    db_session.commit()
    backend = _backend()
    backend.adopt_existing.side_effect = NotImplementedError

    def object_info(key: str):
        if key == keys[0]:
            raise RuntimeError("storage unavailable")
        return None

    backend.object_info.side_effect = object_info

    assert source_covers.reconcile_pending(db_session, backend) == 1
    assert db_session.get(ModelSourceCover, covers[0].id) is not None
    assert db_session.get(StagingLease, leases[0].id) is not None
    assert db_session.get(ModelSourceCover, covers[1].id) is None
    assert db_session.get(StagingLease, leases[1].id) is None


def test_commit_failure_rollback_is_noop_without_publication_receipt(
    db_session: Session,
) -> None:
    source = _source(db_session)
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/no-publication.webp",
        size_bytes=4,
    )
    db_session.add(cover)
    db_session.flush()
    backend = _backend()
    result = source_covers.SourceCoverWrite(cover=cover, created=False)

    source_covers.rollback_after_commit_failure(db_session, backend, result)

    backend.rollback_create.assert_not_called()
    backend.replace_bytes.assert_not_called()
    assert db_session.get(ModelSourceCover, cover.id) is cover


@pytest.mark.parametrize(
    "object_info",
    [StorageObjectInfo(size=7), RuntimeError("storage unavailable")],
)
def test_expired_cover_intent_never_deletes_mismatched_or_uncertain_storage(
    db_session: Session, object_info: object
) -> None:
    source = _source(db_session)
    db_session.commit()
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/foreign.webp",
        size_bytes=4,
    )
    db_session.add(cover)
    db_session.flush()
    lease = staging_leases.create_cover_lease(
        db_session,
        model_source_cover_id=cover.id or 0,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=4,
        sha256="b" * 64,
    )
    lease.expires_at = lease.created_at
    db_session.commit()
    backend = _backend()
    backend.adopt_existing.side_effect = NotImplementedError
    if isinstance(object_info, Exception):
        backend.object_info.side_effect = object_info
    else:
        backend.object_info.return_value = object_info

    assert staging_leases.prune_expired(
        db_session, now=lease.expires_at, backend=backend
    ) == (0, 0)
    assert db_session.get(ModelSourceCover, cover.id) is not None
    assert db_session.get(StagingLease, lease.id) is not None
    backend.rollback_create.assert_not_called()


def test_expired_replacement_intent_keeps_existing_cover_when_old_bytes_remain(
    db_session: Session,
) -> None:
    source = _source(db_session)
    db_session.commit()
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/replacement.webp",
        size_bytes=3,
    )
    db_session.add(cover)
    db_session.flush()
    old = _receipt(key=cover.storage_key, token="old")
    record_creation(db_session, old, object_kind="model_source_cover")
    lease = staging_leases.create_cover_lease(
        db_session,
        model_source_cover_id=cover.id or 0,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=5,
        sha256="c" * 64,
    )
    lease.expires_at = lease.created_at
    db_session.commit()
    backend = _backend()
    backend.adopt_existing.side_effect = NotImplementedError
    backend.object_info.return_value = StorageObjectInfo(size=3)

    assert staging_leases.prune_expired(
        db_session, now=lease.expires_at, backend=backend
    ) == (0, 0)
    assert db_session.get(ModelSourceCover, cover.id) is not None
    assert db_session.get(StagingLease, lease.id) is not None


def test_finish_import_uses_only_intent_and_final_sqlite_commits(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover publication must not open a second writer behind Inbox flushes."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'finish-import.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    commits: list[object] = []

    @event.listens_for(engine, "commit")
    def _count_commit(_connection) -> None:
        commits.append(object())

    factory = SQLiteSessionFactory(engine)
    data = _png()
    with Session(engine) as setup:
        owner = User(username="finish-owner", hashed_password="hash")
        setup.add(owner)
        setup.flush()
        model = Model(
            name="finish-model",
            slug=f"finish-{uuid.uuid4().hex}",
            hash=uuid.uuid4().hex * 2,
        )
        setup.add(model)
        setup.flush()
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="test",
            canonical_url="https://example.test/finish",
            identity_key=uuid.uuid4().hex * 2,
        )
        row = InboxItem(
            owner_user_id=owner.id,
            source_kind=InboxSourceKind.BROWSER,
            source_url=source.canonical_url,
            state="importing",
            manifest_json=json.dumps(
                {
                    "source": {
                        "provider": source.provider,
                        "canonical_url": source.canonical_url,
                        "source_item_id": None,
                    }
                }
            ),
        )
        setup.add_all([source, row])
        setup.flush()
        slot = CaptureUploadSlot(
            id="finish-cover",
            inbox_item_id=row.id,
            role="cover",
            filename="cover.png",
            media_type="image/png",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            state=CaptureUploadSlotState.UPLOADED,
            storage_key="opaque/slot-cover",
        )
        setup.add(slot)
        setup.commit()
        row_id = row.id
        model_id = model.id
        source_id = source.id
    commits.clear()

    backend = _backend()
    backend.source_cover_key.side_effect = lambda ident: f"opaque/cover/{ident}"
    backend.read_bytes.return_value = data
    processed = process_source_cover_upload(data, "image/png")
    backend.create_bytes.return_value = CreationReceipt(
        key=f"opaque/cover/{source_id}",
        size=len(processed.data),
        token="finish-cover",
        backend="fake",
        namespace="test",
    )
    job = type(
        "Job",
        (),
        {"state": "completed", "model_id": model_id, "result": None},
    )()
    monkeypatch.setattr(inbox.registry, "get", lambda _job_id: job)
    monkeypatch.setattr(inbox, "get_backend", lambda: backend)
    monkeypatch.setattr(inbox, "_record_v2_results", lambda *_args: (True, 1, 0))
    monkeypatch.setattr(inbox, "_cleanup_capture_slots", lambda *_args: True)

    inbox._finish_import(row_id, "finish-job", factory)

    assert len(commits) == 2  # durable intent + final Inbox terminalization
    with Session(engine) as check:
        finished = check.get(InboxItem, row_id)
        assert finished is not None
        assert finished.state.value == "completed"
        assert (
            check.exec(select(ModelSourceCover))
            .one()
            .storage_key.startswith("opaque/cover/")
        )
    engine.dispose()
