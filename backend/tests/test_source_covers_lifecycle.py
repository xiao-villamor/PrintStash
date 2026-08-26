"""Failure-safe cover publishing uses only backend seams, never local paths."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.services import inbox, source_covers, staging_leases, trash
from app.services.source_cover_processing import process_source_cover_upload
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageObjectInfo,
    get_backend,
)
from app.services.storage_ownership import record_creation


def _png(color: str = "navy") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def _source(session: Session) -> ModelProvenanceSource:
    ident = uuid.uuid4().hex
    model = Model(
        name=f"Cover model {ident}", slug=f"cover-model-{ident}", hash=ident * 2
    )
    session.add(model)
    session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="test",
        canonical_url="https://example.test/cover",
        identity_key=uuid.uuid4().hex * 2,
    )
    session.add(source)
    session.flush()
    return source


def _backend() -> MagicMock:
    backend = MagicMock(spec=StorageBackend)
    backend.source_cover_key.side_effect = lambda ident: f"opaque/covers/{ident}.webp"
    return backend


def _receipt(key: str = "opaque/covers/1.webp", token: str = "new") -> CreationReceipt:
    return CreationReceipt(
        key=key, size=10, token=token, backend="fake", namespace="test"
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
