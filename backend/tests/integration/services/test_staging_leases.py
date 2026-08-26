"""Defends ``test_review_lease_rejects_replaced_path_without_unlink`` behavior for the ``services`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from alembic import command
from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    StagingLease,
    User,
)
from app.services import staging_leases
from app.services.auth import hash_password
from app.services.storage_backend import CreationReceipt, StorageBackend
from tests.paths import BACKEND_ROOT


def _user(session: Session) -> User:
    user = User(username="lease-user", hashed_password=hash_password("Password123"))
    session.add(user)
    session.flush()
    return user


def _inbox(session: Session, user: User) -> InboxItem:
    row = InboxItem(owner_user_id=user.id)
    session.add(row)
    session.flush()
    return row


def _job(session: Session, user: User) -> BackgroundJob:
    row = BackgroundJob(id="lease-job", owner_user_id=user.id)
    session.add(row)
    session.flush()
    return row


def _capture_slot(
    session: Session,
    user: User,
    inbox: InboxItem,
    *,
    slot_id: str,
    data: bytes,
) -> tuple[CaptureUploadSlot, StagingLease]:
    slot = CaptureUploadSlot(
        id=slot_id,
        inbox_item_id=inbox.id,
        role="file",
        source_file_id=slot_id,
        filename=f"{slot_id}.3mf",
        media_type="application/octet-stream",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=f"capture-slots/{slot_id}",
    )
    session.add(slot)
    session.flush()
    lease = staging_leases.create_capture_slot_lease(
        session,
        slot_id=slot.id,
        owner_user_id=user.id,
        destination_key=slot.storage_key or "",
        size_bytes=slot.size_bytes,
        sha256=slot.sha256,
    )
    return slot, lease


def test_review_lease_rejects_replaced_path_without_unlink(
    db_session: Session, tmp_path: Path
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.3mf"
    staged.write_bytes(b"original")
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=8,
        sha256="a" * 64,
    )
    db_session.commit()
    staged.unlink()
    staged.write_bytes(b"replacement")
    assert (
        staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id) is False
    )
    assert staged.read_bytes() == b"replacement"
    # The receipt is stale, so it no longer owns the replacement and releases
    # only its DB accounting; critically, the replacement remains untouched.
    assert db_session.get(StagingLease, lease.id) is None


def test_prune_expired_unlinks_exact_file(db_session: Session, tmp_path: Path) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.gcode"
    staged.write_bytes(b"staged")
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=6,
        sha256="b" * 64,
    )
    lease.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert staging_leases.prune_expired(db_session) == (1, 1)
    db_session.commit()
    assert not staged.exists()
    assert db_session.get(StagingLease, lease.id) is None


def test_fc15_upgrade_and_downgrade_preserve_job_lease_data(tmp_path: Path) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    url = f"sqlite:///{tmp_path / 'staging-lease.sqlite'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "fb14d5e8a7c3")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO background_jobs "
                "(id, visible, kind, state, status_json, replay_safe, attempts, created_at, updated_at) "
                "VALUES ('old-job', 1, 'ingest', 'pending', '{}', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO staging_leases "
                "(id, path, background_job_id, size_bytes, sha256, expires_at, created_at) "
                "VALUES ('old-lease', '/tmp/old', 'old-job', 1, :sha, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"sha": "f" * 64},
        )
    command.upgrade(config, "fc15a6e9b8d4")
    inspector = inspect(engine)
    columns = {
        column["name"]: column for column in inspector.get_columns("staging_leases")
    }
    assert columns["background_job_id"]["nullable"] is True
    assert "inbox_item_id" in columns
    assert "ix_staging_leases_inbox_item_id" in {
        index["name"] for index in inspector.get_indexes("staging_leases")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                )
            ).scalar_one()
            == "old-job"
        )
    command.downgrade(config, "fb14d5e8a7c3")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT background_job_id FROM staging_leases WHERE id = 'old-lease'"
                )
            ).scalar_one()
            == "old-job"
        )
    assert "inbox_item_id" not in {
        column["name"] for column in inspect(engine).get_columns("staging_leases")
    }
    engine.dispose()


def test_transfer_is_atomic_and_preserves_exactly_one_owner(
    db_session: Session, tmp_path: Path
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.stl"
    staged.write_bytes(b"staged")
    staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=6,
        sha256="c" * 64,
    )
    with pytest.raises(staging_leases.StagingLeaseError):
        staging_leases.transfer_inbox_to_job(
            db_session, inbox_item_id=inbox.id, job_id="missing"
        )
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.inbox_item_id == inbox.id)
    ).one()
    assert lease.background_job_id is None
    job = _job(db_session, user)
    transferred = staging_leases.transfer_inbox_to_job(
        db_session, inbox_item_id=inbox.id, job_id=job.id
    )
    assert transferred.inbox_item_id is None
    assert transferred.background_job_id == job.id
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            StagingLease(
                id="invalid-owner",
                path="/tmp/invalid",
                size_bytes=1,
                sha256="d" * 64,
                expires_at=utcnow(),
            )
        )
        db_session.commit()
    db_session.rollback()


def test_inbox_delete_cascades_review_lease(
    db_session: Session, tmp_path: Path
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.obj"
    staged.write_bytes(b"staged")
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=inbox.id,
        owner_user_id=user.id,
        path=staged,
        size_bytes=6,
        sha256="e" * 64,
    )
    lease_id = lease.id
    db_session.commit()
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    db_session.exec(delete(InboxItem).where(InboxItem.id == inbox.id))
    db_session.commit()
    assert db_session.get(StagingLease, lease_id) is None


def test_review_lease_fails_closed_when_capacity_cannot_be_measured(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.3mf"
    staged.write_bytes(b"staged")
    monkeypatch.setattr(
        staging_leases.os,
        "statvfs",
        lambda _path: (_ for _ in ()).throw(OSError("capacity unavailable")),
    )

    with pytest.raises(
        staging_leases.StagingCapacityExceeded,
        match="staging_capacity_unavailable",
    ):
        staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="f" * 64,
        )

    assert db_session.exec(select(StagingLease)).all() == []


def test_review_lease_rejects_missing_inbox_owner(
    db_session: Session, tmp_path: Path
) -> None:
    staged = tmp_path / "capture.3mf"
    staged.write_bytes(b"staged")

    with pytest.raises(staging_leases.StagingLeaseError, match="inbox item"):
        staging_leases.create_review_lease(
            db_session,
            inbox_item_id=999_999,
            owner_user_id=None,
            path=staged,
            size_bytes=6,
            sha256="f" * 64,
        )

    assert db_session.exec(select(StagingLease)).all() == []


def test_review_lease_rejects_path_size_mismatch(
    db_session: Session, tmp_path: Path
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    staged = tmp_path / "capture.3mf"
    staged.write_bytes(b"staged")

    with pytest.raises(staging_leases.StagingLeaseError, match="identity"):
        staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=7,
            sha256="f" * 64,
        )

    assert staged.read_bytes() == b"staged"
    assert db_session.exec(select(StagingLease)).all() == []


def test_capture_slot_stream_uses_exact_owned_spool(
    db_session: Session, tmp_path: Path
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    data = b"captured bytes"
    slot, _lease = _capture_slot(
        db_session, user, inbox, slot_id="owned-stream", data=data
    )
    staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)

    path, size, digest = staging_leases.stage_capture_slot_stream(
        db_session,
        slot_id=slot.id,
        stream=BytesIO(data),
        max_bytes=len(data),
    )

    assert path.read_bytes() == data
    assert size == len(data)
    assert digest == hashlib.sha256(data).hexdigest()


def test_capture_slot_removal_preserves_replaced_spool(
    db_session: Session, tmp_path: Path
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    slot, _lease = _capture_slot(
        db_session, user, inbox, slot_id="replaced-spool", data=b"owned"
    )
    path = staging_leases.prepare_capture_slot_staging(db_session, slot_id=slot.id)
    path.unlink()
    path.write_bytes(b"foreign")

    removed = staging_leases.remove_capture_slot_staging(db_session, slot_id=slot.id)

    assert removed is False
    assert path.read_bytes() == b"foreign"


def test_capture_slot_reconciliation_persists_exact_adoption_receipt(
    db_session: Session, tmp_path: Path
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    data = b"published"
    slot, lease = _capture_slot(
        db_session, user, inbox, slot_id="adopted-slot", data=data
    )
    receipt = CreationReceipt(
        key=slot.storage_key or "",
        size=len(data),
        token="adopted",
        backend="fake",
        namespace="test",
    )
    backend = MagicMock(spec=StorageBackend)
    backend.adopt_existing.return_value = receipt

    recovered = staging_leases.reconcile_capture_slot(db_session, backend, slot)

    assert recovered is True
    assert slot.state == CaptureUploadSlotState.UPLOADED
    assert slot.receipt_json == lease.receipt_json
    assert '"token": "adopted"' in (slot.receipt_json or "")


@pytest.mark.parametrize(
    "receipt",
    [
        pytest.param(
            CreationReceipt(
                key="capture-slots/wrong",
                size=9,
                token="wrong-key",
                backend="fake",
                namespace="test",
            ),
            id="wrong-key",
        ),
        pytest.param(
            CreationReceipt(
                key="capture-slots/mismatched-adoption",
                size=8,
                token="wrong-size",
                backend="fake",
                namespace="test",
            ),
            id="wrong-size",
        ),
    ],
)
def test_capture_slot_reconciliation_rejects_mismatched_adoption(
    db_session: Session,
    tmp_path: Path,
    receipt: CreationReceipt,
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    slot, _lease = _capture_slot(
        db_session, user, inbox, slot_id="mismatched-adoption", data=b"published"
    )
    backend = MagicMock(spec=StorageBackend)
    backend.adopt_existing.return_value = receipt

    recovered = staging_leases.reconcile_capture_slot(db_session, backend, slot)

    assert recovered is False
    assert slot.state == CaptureUploadSlotState.PENDING
    assert slot.receipt_json is None


def test_job_lease_renewal_updates_every_owned_lease(
    db_session: Session, tmp_path: Path
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    first, _first_lease = _capture_slot(
        db_session, user, inbox, slot_id="renew-first", data=b"first"
    )
    second, _second_lease = _capture_slot(
        db_session, user, inbox, slot_id="renew-second", data=b"second"
    )
    job = _job(db_session, user)
    staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=inbox.id, job_id=job.id
    )
    now = datetime(2026, 1, 1, 12, 0, 0)

    returned = staging_leases.renew_job_lease(db_session, job_id=job.id, now=now)

    leases = db_session.exec(
        select(StagingLease).where(StagingLease.background_job_id == job.id)
    ).all()
    assert returned in leases
    assert {lease.capture_upload_slot_origin_id for lease in leases} == {
        first.id,
        second.id,
    }
    assert {lease.expires_at for lease in leases} == {
        now + timedelta(hours=settings.staging_import_lease_hours)
    }


def test_job_lease_renewal_rejects_job_without_lease(db_session: Session) -> None:
    user = _user(db_session)
    job = _job(db_session, user)

    with pytest.raises(staging_leases.StagingLeaseError, match="at least one"):
        staging_leases.renew_job_lease(db_session, job_id=job.id)


def test_capture_slot_lease_dismissal_releases_every_slot(
    db_session: Session, tmp_path: Path
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    _capture_slot(db_session, user, inbox, slot_id="dismiss-first", data=b"first")
    _capture_slot(db_session, user, inbox, slot_id="dismiss-second", data=b"second")

    released = staging_leases.dismiss_capture_slot_leases(
        db_session, inbox_item_id=inbox.id
    )

    assert released is True
    assert db_session.exec(select(StagingLease)).all() == []


def test_capture_slot_lease_dismissal_reports_no_slots(db_session: Session) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)

    released = staging_leases.dismiss_capture_slot_leases(
        db_session, inbox_item_id=inbox.id
    )

    assert released is False


def test_capture_slot_lease_dismissal_rejects_missing_lease(
    db_session: Session,
) -> None:
    user = _user(db_session)
    inbox = _inbox(db_session, user)
    slot = CaptureUploadSlot(
        id="missing-lease",
        inbox_item_id=inbox.id,
        role="file",
        source_file_id="missing-lease",
        filename="missing.3mf",
        media_type="application/octet-stream",
        size_bytes=1,
        sha256="a" * 64,
        storage_key="capture-slots/missing-lease",
    )
    db_session.add(slot)
    db_session.flush()

    with pytest.raises(staging_leases.StagingLeaseError, match="lease missing"):
        staging_leases.dismiss_capture_slot_leases(db_session, inbox_item_id=inbox.id)

    assert db_session.get(CaptureUploadSlot, slot.id) is not None
