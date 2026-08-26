"""Defends create publish failure leaves no cover lease or proof at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._source_covers_lifecycle_shared import (
    BackgroundJob,
    CreationReceipt,
    IntegrityError,
    Model,
    ModelSourceCover,
    OwnedStorageObject,
    Session,
    StagingLease,
    StorageDeleteIntent,
    _backend,
    _png,
    _receipt,
    _source,
    get_backend,
    process_source_cover_upload,
    pytest,
    record_creation,
    select,
    source_covers,
    staging_leases,
    trash,
)


def test_create_publish_failure_leaves_no_cover_lease_or_proof(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = _backend()
    backend.create_bytes.side_effect = RuntimeError("publish failed")

    with pytest.raises(RuntimeError, match="publish failed"):
        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )

    assert db_session.exec(select(ModelSourceCover)).all() == []
    assert db_session.exec(select(StagingLease)).all() == []
    assert db_session.exec(select(OwnedStorageObject)).all() == []


def test_create_rolls_back_published_bytes_when_recording_the_receipt_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(db_session)
    backend = _backend()
    receipt = _receipt(f"opaque/covers/{source.id}.webp")
    backend.create_bytes.return_value = receipt
    monkeypatch.setattr(
        staging_leases,
        "record_cover_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("proof failed")),
    )

    with pytest.raises(RuntimeError, match="proof failed"):
        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )

    backend.rollback_create.assert_called_once_with(receipt)
    assert db_session.exec(select(ModelSourceCover)).all() == []
    assert db_session.exec(select(StagingLease)).all() == []
    assert db_session.exec(select(OwnedStorageObject)).all() == []


def test_replacement_failure_keeps_old_metadata_and_proof(db_session: Session) -> None:
    source = _source(db_session)
    cover = ModelSourceCover(
        provenance_source_id=source.id, storage_key="opaque/covers/1.webp", size_bytes=3
    )
    db_session.add(cover)
    old = _receipt(token="old")
    record_creation(db_session, old, object_kind="model_source_cover")
    db_session.flush()
    backend = _backend()
    backend.read_bytes.return_value = b"old"
    backend.creation_matches.return_value = True
    backend.replace_bytes.side_effect = RuntimeError("replace failed")

    with pytest.raises(RuntimeError, match="replace failed"):
        source_covers.put(
            db_session,
            backend,
            provenance_source_id=source.id,
            actor_id=None,
            data=_png(),
            content_type="image/png",
        )

    assert cover.size_bytes == 3
    proof = db_session.exec(select(OwnedStorageObject)).one()
    assert proof.token == "old"


def test_commit_failure_rolls_back_new_publish_with_exact_receipt(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = _backend()
    receipt = _receipt(f"opaque/covers/{source.id}.webp")
    backend.create_bytes.return_value = receipt
    result = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png(),
        content_type="image/png",
    )
    db_session.rollback()

    source_covers.rollback_after_commit_failure(db_session, backend, result)

    backend.rollback_create.assert_called_once_with(receipt)
    assert db_session.exec(select(ModelSourceCover)).all() == []
    assert db_session.exec(select(StagingLease)).all() == []


def test_replacement_commit_failure_restores_bytes_and_a_current_proof(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = get_backend()
    first = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("navy"),
        content_type="image/png",
    )
    db_session.commit()
    old_bytes = backend.read_bytes(first.cover.storage_key)

    replacement = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("maroon"),
        content_type="image/png",
    )
    db_session.rollback()
    source_covers.rollback_after_commit_failure(db_session, backend, replacement)

    assert backend.read_bytes(first.cover.storage_key) == old_bytes
    proof = db_session.exec(select(OwnedStorageObject)).one()
    assert backend.creation_matches(
        CreationReceipt(
            key=proof.key,
            size=proof.size_bytes,
            token=proof.token,
            backend=proof.backend,
            namespace=proof.namespace,
            etag=proof.etag,
            version_id=proof.version_id,
            device=proof.device,
            inode=proof.inode,
            ctime_ns=proof.ctime_ns,
        )
    )


def test_successive_replacements_publish_latest_bytes_and_release_each_lease(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = get_backend()

    first = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("navy"),
        content_type="image/png",
    )
    db_session.commit()

    second = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("maroon"),
        content_type="image/png",
    )
    assert second.cover.id == first.cover.id
    assert db_session.exec(select(StagingLease)).all() == []
    db_session.commit()

    latest = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("gold"),
        content_type="image/png",
    )
    db_session.commit()

    assert latest.cover.id == first.cover.id
    expected = process_source_cover_upload(_png("gold"), "image/png").data
    assert backend.read_bytes(latest.cover.storage_key) == expected
    assert db_session.exec(select(StagingLease)).all() == []


def test_new_replacement_supersedes_a_crashed_prior_generation(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = get_backend()
    first = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("navy"),
        content_type="image/png",
    )
    db_session.commit()

    source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("maroon"),
        content_type="image/png",
    )
    # Simulate a process crash after publication but before the caller's
    # transaction commits its replacement metadata and lease release.
    db_session.rollback()
    assert db_session.exec(select(StagingLease)).one()

    latest = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("gold"),
        content_type="image/png",
    )
    db_session.commit()

    expected = process_source_cover_upload(_png("gold"), "image/png").data
    assert latest.cover.id == first.cover.id
    assert backend.read_bytes(latest.cover.storage_key) == expected
    assert db_session.exec(select(StagingLease)).all() == []


def test_soft_delete_and_restore_keep_cover_and_proof(db_session: Session) -> None:
    source = _source(db_session)
    model = db_session.get(Model, source.model_id)
    assert model is not None
    backend = get_backend()
    result = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png(),
        content_type="image/png",
    )
    db_session.commit()

    trash.soft_delete_model(db_session, model)
    assert db_session.get(ModelSourceCover, result.cover.id) is not None
    assert (
        db_session.exec(select(OwnedStorageObject)).one().key
        == result.cover.storage_key
    )
    trash.restore_model(db_session, model)
    assert db_session.get(ModelSourceCover, result.cover.id) is not None
    assert backend.read_bytes(result.cover.storage_key)


def test_hard_delete_enqueues_one_required_proof_intent_for_cover(
    db_session: Session,
) -> None:
    source = _source(db_session)
    model = db_session.get(Model, source.model_id)
    assert model is not None
    backend = get_backend()
    result = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png(),
        content_type="image/png",
    )
    db_session.commit()

    trash.soft_delete_model(db_session, model)
    trash.hard_delete_model(db_session, model)
    db_session.commit()

    intents = db_session.exec(
        select(StorageDeleteIntent).where(
            StorageDeleteIntent.resource_kind == "model_source_cover"
        )
    ).all()
    assert len(intents) == 1
    assert intents[0].key == result.cover.storage_key
    assert intents[0].resource_id == str(result.cover.id)


def test_cover_lease_requires_cover_as_its_only_owner_and_cascades(
    db_session: Session,
) -> None:
    source = _source(db_session)
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/cascade.webp",
        size_bytes=3,
    )
    db_session.add(cover)
    db_session.flush()
    assert cover.id is not None
    lease = staging_leases.create_cover_lease(
        db_session,
        model_source_cover_id=cover.id,
        owner_user_id=None,
        destination_key=cover.storage_key,
        size_bytes=3,
        sha256="a" * 64,
    )
    lease_id = lease.id
    db_session.commit()
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.delete(cover)
    db_session.commit()
    assert db_session.get(StagingLease, lease_id) is None

    replacement = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/conflict.webp",
        size_bytes=1,
    )
    job = BackgroundJob(id="cover-owner-conflict")
    db_session.add_all([replacement, job])
    db_session.commit()
    assert replacement.id is not None
    invalid = StagingLease(
        id="cover-owner-conflict",
        path="cover:invalid",
        background_job_id=job.id,
        model_source_cover_id=replacement.id,
        size_bytes=1,
        sha256="a" * 64,
        expires_at=cover.created_at,
    )
    db_session.add(invalid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_restart_reconciles_cover_published_before_receipt_commit(
    db_session: Session,
) -> None:
    """A crash after create-only publication leaves a recoverable cover intent."""
    source = _source(db_session)
    backend = get_backend()
    data = _png("navy")
    result = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=data,
        content_type="image/png",
    )
    assert result.cover.id is not None
    # The caller's transaction is the receipt/proof commit boundary. A
    # process crash here rolls back that transaction, while the precommitted
    # cover + lease and published bytes survive.
    db_session.rollback()

    assert backend.exists(result.cover.storage_key)
    assert db_session.exec(select(StagingLease)).all()
    assert source_covers.reconcile_pending(db_session, backend) == 1
    db_session.commit()

    assert db_session.exec(select(StagingLease)).all() == []
    assert (
        db_session.exec(select(OwnedStorageObject)).one().key
        == result.cover.storage_key
    )
    assert backend.read_bytes(result.cover.storage_key)


def test_restart_reconciles_replacement_without_restoring_old_bytes(
    db_session: Session,
) -> None:
    source = _source(db_session)
    backend = get_backend()
    first = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png("navy"),
        content_type="image/png",
    )
    db_session.commit()
    replacement_data = _png("maroon")
    source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=replacement_data,
        content_type="image/png",
    )
    db_session.rollback()

    normalized = process_source_cover_upload(replacement_data, "image/png").data
    assert backend.read_bytes(first.cover.storage_key) == normalized
    assert source_covers.reconcile_pending(db_session, backend) == 1
    db_session.commit()

    cover = db_session.get(ModelSourceCover, first.cover.id)
    assert cover is not None
    assert cover.size_bytes == len(normalized)
    proof = db_session.exec(select(OwnedStorageObject)).one()
    assert backend.creation_matches(
        CreationReceipt(
            key=proof.key,
            size=proof.size_bytes,
            token=proof.token,
            backend=proof.backend,
            namespace=proof.namespace,
            etag=proof.etag,
            version_id=proof.version_id,
            device=proof.device,
            inode=proof.inode,
            ctime_ns=proof.ctime_ns,
        )
    )


def test_cover_intent_does_not_commit_callers_unrelated_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(db_session)
    db_session.commit()
    caller_commit = db_session.commit
    commits: list[object] = []
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: commits.append(object()),
    )
    backend = get_backend()
    result = source_covers.put(
        db_session,
        backend,
        provenance_source_id=source.id,
        actor_id=None,
        data=_png(),
        content_type="image/png",
    )

    assert result.cover.id is not None
    assert commits == []
    monkeypatch.setattr(db_session, "commit", caller_commit)
    db_session.rollback()


def test_expired_cover_intent_without_bytes_removes_cover_and_lease(
    db_session: Session,
) -> None:
    source = _source(db_session)
    db_session.commit()
    cover = ModelSourceCover(
        provenance_source_id=source.id,
        storage_key="opaque/covers/not-published.webp",
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
    lease.expires_at = lease.created_at
    db_session.commit()
    backend = _backend()
    backend.adopt_existing.side_effect = NotImplementedError
    backend.object_info.return_value = None

    assert staging_leases.prune_expired(
        db_session, now=lease.expires_at, backend=backend
    ) == (1, 0)
    assert db_session.get(ModelSourceCover, cover.id) is None
    assert db_session.get(StagingLease, lease.id) is None
    backend.rollback_create.assert_not_called()
