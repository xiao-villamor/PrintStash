"""Dismissing a capture without abandoning — or destroying — the bytes it staged.

A capture holds real files in the staging directory before anything is imported,
and the lease is the only record saying they are PrintStash's. Dismissal has to
reconcile the two, and the honest answer differs by case:

- **The bytes are gone already.** Nothing left to own, so the receipt is released
  and the item dismissed. Keeping the row would leave a capture the user cannot
  get rid of, over a file that no longer exists.
- **The bytes are there and we own them.** Delete both, and — for an upload slot,
  whose bytes live in the storage backend rather than on disk — schedule the blob
  delete so the object does not outlive its row.
- **The bytes are there and we cannot prove they are ours.** Refuse the dismissal
  with `409 staging_cleanup_failed` and touch nothing. A path with no lease behind
  it is somebody else's file at a predictable location, and staging is shared.
- **Two leases claim the same job.** Ambiguous, so also a refusal: acting on
  either one means guessing which file the item was about.

The other half of every case is that the *library* is untouched. A completed
capture has already produced a Model and its artifacts, and dismissing the queue
entry must never take those with it — the queue is a to-do list, not the vault.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    StagingLease,
    User,
)
from app.services import inbox
from app.services.storage_deletion import process_storage_delete_intents
from tests.factories import build_file, build_model
from tests.integration.api.v1.inbox.conftest import CANONICAL_URL, slot_payload


@pytest.fixture(autouse=True)
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage into a temp directory so a refused cleanup leaves it inspectable."""
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    return tmp_path


def _browser_capture(session: Session, owner: User) -> InboxItem:
    """A staged browser upload: real bytes on disk, with a lease over them."""
    return inbox.create_browser_upload(
        session,
        owner,
        source_url=CANONICAL_URL,
        title="Widget",
        capture_source=None,
        filename="widget.stl",
        stream=BytesIO(b"staged-widget"),
    )


def _lease_for_item(session: Session, item_id: int) -> StagingLease:
    return session.exec(
        select(StagingLease).where(StagingLease.inbox_item_id == item_id)
    ).one()


def _leases_for_item(session: Session, item_id: int) -> list[StagingLease]:
    return list(
        session.exec(select(StagingLease).where(StagingLease.inbox_item_id == item_id))
    )


class TestUploadSlotCapture:
    def test_dismisses_the_item(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        owner = make_user("dismiss-uploaded-capture")
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, slot_payload()
        )
        inbox.upload_capture_slot(
            db_session,
            slots[0],
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )
        row.state = InboxItemState.COMPLETED
        db_session.add(row)
        db_session.commit()

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(InboxItem, row.id).state == InboxItemState.DISMISSED

    def test_releases_the_upload_slot(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        # The slot is the reservation the extension uploaded against; leaving it
        # behind is a row pointing at a capture nobody can reach any more.
        owner = make_user("dismiss-uploaded-slot")
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, slot_payload()
        )
        slot = inbox.upload_capture_slot(
            db_session,
            slots[0],
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )
        slot_id = slot.id
        row.state = InboxItemState.COMPLETED
        db_session.add(row)
        db_session.commit()

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        assert db_session.get(CaptureUploadSlot, slot_id) is None

    def test_schedules_the_staged_blob_for_deletion(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        """The slot's bytes live in the storage backend, not on disk.

        Dropping the row without an intent leaves an object nothing references and
        nothing will ever collect — it is only visible as storage that grows.
        """
        owner = make_user("dismiss-uploaded-blob")
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, slot_payload()
        )
        slot = inbox.upload_capture_slot(
            db_session,
            slots[0],
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )
        assert slot.storage_key is not None
        storage_key = slot.storage_key
        row.state = InboxItemState.COMPLETED
        db_session.add(row)
        db_session.commit()

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert process_storage_delete_intents().completed == 1
        assert not inbox.get_backend().exists(storage_key)

    def test_keeps_the_model_the_capture_already_imported(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        # The queue is a to-do list; dismissing an entry is not a delete.
        owner = make_user("dismiss-uploaded-keeps-model")
        row, slots = inbox.create_capture_upload_slots(
            db_session, owner, slot_payload()
        )
        inbox.upload_capture_slot(
            db_session,
            slots[0],
            stream=BytesIO(b"slot-owned"),
            media_type="application/octet-stream",
        )
        model = build_model(
            db_session,
            name="Imported widget",
            slug="dismiss-uploaded-widget",
            hash="f" * 64,
        )
        artifact = build_file(
            db_session,
            model,
            path="imported/dismiss-uploaded-widget.stl",
            filename="widget.stl",
            file_type=FileType.STL,
            size_bytes=4,
            sha256="a" * 64,
        )
        row.state = InboxItemState.COMPLETED
        row.resulting_model_id = model.id
        db_session.add_all([row, artifact])
        db_session.commit()
        db_session.refresh(artifact)

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        assert db_session.get(File, artifact.id) is not None


class TestStagingAlreadyGone:
    def test_dismisses_an_item_whose_staged_file_vanished(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        # Nothing left to own, so refusing would leave an undismissable capture.
        owner = make_user("dismiss-missing-staging")
        row = _browser_capture(db_session, owner)
        assert row.staging_key is not None
        Path(row.staging_key).unlink()

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(InboxItem, row.id).state == InboxItemState.DISMISSED

    def test_releases_the_receipt_for_a_file_that_vanished(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        owner = make_user("dismiss-missing-lease")
        row = _browser_capture(db_session, owner)
        assert row.staging_key is not None
        Path(row.staging_key).unlink()

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        assert _leases_for_item(db_session, row.id) == []

    def test_dismisses_an_expired_capture_that_kept_neither(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        """A capture whose staging expired has no key and no lease left.

        This is the state the expiry sweeper leaves behind, so it is the state a
        user most often presses Dismiss on. Refusing it would strand every failed
        capture in the queue permanently.
        """
        owner = make_user("dismiss-expired-staging")
        row = _browser_capture(db_session, owner)
        assert row.staging_key is not None
        Path(row.staging_key).unlink()
        db_session.delete(_lease_for_item(db_session, row.id))
        row.staging_key = None
        row.state = InboxItemState.FAILED
        row.error_code = "staging_expired"
        db_session.add(row)
        db_session.commit()

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(InboxItem, row.id).state == InboxItemState.DISMISSED


class TestStagingItCannotProveItOwns:
    def test_refuses_a_staged_path_with_no_lease_behind_it(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        """Staging is shared, so an unproven path may be somebody else's file.

        Deleting it on the strength of a filename is how one user's dismissal
        removes another user's in-flight capture.
        """
        owner = make_user("dismiss-unowned-staging")
        row = _browser_capture(db_session, owner)
        db_session.delete(_lease_for_item(db_session, row.id))
        db_session.commit()

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "staging_cleanup_failed"

    def test_leaves_the_unproven_file_where_it_is(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        owner = make_user("dismiss-unowned-untouched")
        row = _browser_capture(db_session, owner)
        assert row.staging_key is not None
        staged = Path(row.staging_key)
        db_session.delete(_lease_for_item(db_session, row.id))
        db_session.commit()

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert staged.exists()

    def test_keeps_the_item_in_the_queue_after_a_refusal(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        # A 409 that still dismissed the row would hide the fact that bytes were
        # left behind, and the user has no other way to find out.
        owner = make_user("dismiss-unowned-retained")
        row = _browser_capture(db_session, owner)
        db_session.delete(_lease_for_item(db_session, row.id))
        db_session.commit()

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        assert db_session.get(InboxItem, row.id).state == InboxItemState.REVIEW


class TestAmbiguousJobLeases:
    @pytest.fixture
    def two_leases(
        self, db_session: Session, tmp_path: Path, make_user, headers_for
    ) -> tuple[InboxItem, BackgroundJob, list[Path], User]:
        """One job holding two leases — the state a retried capture can leave."""
        owner = make_user("dismiss-ambiguous-job-leases")
        job = BackgroundJob(
            id="dismiss-ambiguous-job",
            owner_user_id=owner.id,
            state="failed",
            status_json='{"state":"failed"}',
        )
        db_session.add(job)
        db_session.commit()

        paths: list[Path] = []
        for index, name in enumerate(("first.stl", "second.stl")):
            path = tmp_path / name
            path.write_bytes(f"staged-{name}".encode())
            stat = path.stat()
            db_session.add(
                StagingLease(
                    id=f"ambiguous-lease-{index}",
                    path=str(path),
                    owner_user_id=owner.id,
                    background_job_id=job.id,
                    size_bytes=stat.st_size,
                    sha256="d" * 64,
                    device=stat.st_dev,
                    inode=stat.st_ino,
                    ctime_ns=stat.st_ctime_ns,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            paths.append(path)

        row = InboxItem(
            owner_user_id=owner.id,
            source_url=CANONICAL_URL,
            source_hostname="makerworld.com",
            source_kind=InboxSourceKind.BROWSER,
            state=InboxItemState.FAILED,
            background_job_id=job.id,
            staging_key=None,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row, job, paths, owner

    def test_refuses_rather_than_guessing_which_file_it_meant(
        self, client: TestClient, db_session: Session, headers_for, two_leases
    ) -> None:
        row, _job, _paths, owner = two_leases

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "staging_cleanup_failed"

    def test_keeps_both_files(
        self, client: TestClient, db_session: Session, headers_for, two_leases
    ) -> None:
        row, _job, paths, owner = two_leases

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert [path.exists() for path in paths] == [True, True]

    def test_keeps_both_receipts(
        self, client: TestClient, db_session: Session, headers_for, two_leases
    ) -> None:
        row, job, _paths, owner = two_leases

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        retained = db_session.exec(
            select(StagingLease).where(StagingLease.background_job_id == job.id)
        ).all()
        assert {lease.id for lease in retained} == {
            "ambiguous-lease-0",
            "ambiguous-lease-1",
        }

    def test_leaves_the_item_exactly_as_it_was(
        self, client: TestClient, db_session: Session, headers_for, two_leases
    ) -> None:
        row, job, _paths, owner = two_leases

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        db_session.expire_all()
        retained = db_session.get(InboxItem, row.id)
        assert retained.state == InboxItemState.FAILED
        assert retained.background_job_id == job.id
        assert retained.staging_key is None
