"""The receipt that proves PrintStash owns a file in the staging directory.

Staging holds bytes that are not yet library artifacts, and the lease is the only
thing that says which of them are ours. Every operation here is therefore about
*exactly one owner*: a transfer between owners is atomic and leaves precisely one,
because zero means bytes nothing will clean up and two means two callers each
believe they may delete the same file.

The prune rows are the dangerous side. Expiry unlinks the **exact** recorded file,
proven by device and inode — and refuses to unlink a path that has been replaced
since the lease was written, without deleting it. A replaced path is somebody
else's file at a predictable location.

The cascade row exists because a lease outliving its Inbox item is a leak, and the
migration row because lease data has to survive an upgrade in both directions:
losing it turns staged uploads into unownable bytes.

The matcher rows at the bottom are where that proof is actually enforced, and they
are the reason the rest is safe. The recorded path is a string; by the time a lease
expires, the file at that path may have been replaced, may be a symlink into
somebody's library, or may be a directory. So every lease carries the device, inode
and ctime of the file it was taken on, and the matcher hands back a path **only**
when all of them still agree. Everything else returns `None`, and `None` means
"leave it alone", never "delete it anyway".

Capture spools are the one exception, and a deliberate one: their bytes are written
into the recorded inode while the request is in flight, so size and ctime
legitimately change. Device and inode still hold, and on a filesystem that supports
it an extended attribute carries the slot id — so a *replacement* at the same
deterministic path, even one that reuses the inode number, is refused. On a
filesystem without xattrs the device/inode proof is the fallback, and the path is
still never recursively scanned.
"""

from __future__ import annotations

import errno
import os
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from alembic import command
from app.core.time import utcnow
from app.db.models import BackgroundJob, InboxItem, StagingLease, User
from app.services import staging_leases
from tests.factories import build_user
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


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


class TestEntryHelpers:
    @pytest.mark.parametrize("receipt_id", ["", "unsafe/receipt"])
    def test_unsafe_receipt_id_has_no_quarantine_destination(
        self, tmp_path: Path, receipt_id: str
    ) -> None:
        assert (
            staging_leases._quarantine_entry_path(tmp_path / "upload", receipt_id)
            is None
        )

    def test_absent_quarantine_destination_is_not_present(self) -> None:
        assert staging_leases._entry_present(None) is False


class TestTransfer:
    def test_a_transfer_leaves_exactly_one_owner(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-user")
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


class TestCascade:
    def test_inbox_delete_cascades_review_lease(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-user")
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
        db_session.exec(delete(InboxItem).where(InboxItem.id == inbox.id))
        db_session.commit()
        assert db_session.get(StagingLease, lease_id) is None


class TestPruneExpired:
    def test_prune_expired_unlinks_exact_file(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-user")
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

    def test_prune_preserves_replacement_with_expired_lease(
        self,
        db_session: Session,
        tmp_path: Path,
    ) -> None:
        user = build_user(db_session, "lease-prune-replacement")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "replacement.gcode"
        staged.write_bytes(b"original")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=8,
            sha256="b" * 64,
        )
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        staged.unlink()
        staged.write_bytes(b"replacement")

        assert staging_leases.prune_expired(db_session) == (0, 0)
        assert staged.read_bytes() == b"replacement"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_prune_preserves_both_objects_when_path_changes_during_quarantine(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-prune-quarantine-race")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "race.gcode"
        staged.write_bytes(b"original")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=8,
            sha256="b" * 64,
        )
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        moved_elsewhere = tmp_path / "prune-moved.gcode"
        real_rename = staging_leases.os.rename

        def race_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
            real_rename(staged, moved_elsewhere)
            staged.write_bytes(b"replacement")
            return real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(staging_leases.os, "rename", race_rename)

        assert staging_leases.prune_expired(db_session) == (0, 0)
        assert staged.read_bytes() == b"replacement"
        assert moved_elsewhere.read_bytes() == b"original"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_prune_retries_a_retained_quarantine_after_unlink_failure(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-prune-retry")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "retry.gcode"
        staged.write_bytes(b"original")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=8,
            sha256="b" * 64,
        )
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        real_unlink = staging_leases.os.unlink
        failures = 0

        def fail_once(path, *args, **kwargs):
            nonlocal failures
            if str(path).endswith(".entry") and failures == 0:
                failures += 1
                raise OSError("quarantine unlink failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(staging_leases.os, "unlink", fail_once)
        assert staging_leases.prune_expired(db_session) == (0, 0)
        assert db_session.get(StagingLease, lease.id) is not None
        assert list(tmp_path.rglob("*.entry"))

        monkeypatch.setattr(staging_leases.os, "unlink", real_unlink)
        assert staging_leases.prune_expired(db_session) == (1, 1)
        assert db_session.get(StagingLease, lease.id) is None
        assert not list(tmp_path.rglob("*.entry"))


class TestUnlink:
    def test_review_lease_rejects_replaced_path_without_unlink(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-user")
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
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert staged.read_bytes() == b"replacement"
        # The receipt is stale, so it no longer owns the replacement. Keep the
        # lease charged until an operator/retry can prove the original is gone.
        assert db_session.get(StagingLease, lease.id) is not None

    def test_dismiss_preserves_both_objects_when_path_changes_during_quarantine(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-quarantine-race")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "race.3mf"
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
        moved_elsewhere = tmp_path / "moved-by-writer.3mf"
        real_rename = staging_leases.os.rename

        def race_rename(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
            real_rename(staged, moved_elsewhere)
            staged.write_bytes(b"replacement")
            return real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(staging_leases.os, "rename", race_rename)

        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert staged.read_bytes() == b"replacement"
        assert moved_elsewhere.read_bytes() == b"original"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_dismiss_keeps_lease_when_quarantine_removal_fails(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-quarantine-failure")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "failure.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="a" * 64,
        )
        db_session.commit()
        real_unlink = staging_leases.os.unlink

        def fail_quarantine_unlink(path, *args, **kwargs):
            if str(path).endswith(".entry"):
                raise OSError("quarantine filesystem failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(staging_leases.os, "unlink", fail_quarantine_unlink)

        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert db_session.get(StagingLease, lease.id) is not None
        assert not staged.exists()
        assert list(tmp_path.rglob("*.entry"))

    def test_dismiss_retries_a_retained_quarantine_after_unlink_failure(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-dismiss-retry")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "retry.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="a" * 64,
        )
        db_session.commit()
        real_unlink = staging_leases.os.unlink
        failures = 0

        def fail_once(path, *args, **kwargs):
            nonlocal failures
            if str(path).endswith(".entry") and failures == 0:
                failures += 1
                raise OSError("quarantine unlink failure")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(staging_leases.os, "unlink", fail_once)
        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert db_session.get(StagingLease, lease.id) is not None
        assert list(tmp_path.rglob("*.entry"))

        monkeypatch.setattr(staging_leases.os, "unlink", real_unlink)
        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is True
        )
        assert db_session.get(StagingLease, lease.id) is None
        assert not list(tmp_path.rglob("*.entry"))

    def test_dismiss_retries_after_quarantine_fsync_failure(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-dismiss-fsync-retry")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "fsync-retry.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="a" * 64,
        )
        db_session.commit()
        (tmp_path / ".printstash-staging-quarantine").mkdir(mode=0o700)
        real_fsync = staging_leases.os.fsync
        failures = 0

        def fail_once(fd):
            nonlocal failures
            if failures == 0:
                failures += 1
                raise OSError("quarantine fsync failure")
            return real_fsync(fd)

        monkeypatch.setattr(staging_leases.os, "fsync", fail_once)
        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert db_session.get(StagingLease, lease.id) is not None
        assert list(tmp_path.rglob("*.entry"))

        monkeypatch.setattr(staging_leases.os, "fsync", real_fsync)
        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is True
        )
        assert db_session.get(StagingLease, lease.id) is None
        assert not list(tmp_path.rglob("*.entry"))

    def test_dismiss_preserves_replacement_after_final_proof(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = build_user(db_session, "lease-final-proof-race")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "final-proof.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="a" * 64,
        )
        db_session.commit()
        real_stat = staging_leases.os.stat
        q_stats = 0

        def race_stat(path, *args, **kwargs):
            nonlocal q_stats
            info = real_stat(path, *args, **kwargs)
            if str(path).endswith(".entry") and kwargs.get("dir_fd") is not None:
                q_stats += 1
                if q_stats == 2:
                    staged.write_bytes(b"replacement")
            return info

        monkeypatch.setattr(staging_leases.os, "stat", race_stat)

        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is True
        )
        assert staged.read_bytes() == b"replacement"
        assert db_session.get(StagingLease, lease.id) is None

    def test_dismiss_preserves_a_symlink_replacement(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        user = build_user(db_session, "lease-symlink-replacement")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "symlink.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="a" * 64,
        )
        db_session.commit()
        target = tmp_path / "outside.3mf"
        target.write_bytes(b"outside")
        staged.unlink()
        staged.symlink_to(target)

        assert (
            staging_leases.dismiss_review_lease(db_session, inbox_item_id=inbox.id)
            is False
        )
        assert staged.is_symlink()
        assert target.read_bytes() == b"outside"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_review_lease_releases_accounting_when_path_is_already_gone(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        """A missing exact path releases stale accounting idempotently."""
        user = build_user(db_session, "lease-user")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "missing.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="b" * 64,
        )
        db_session.commit()
        staged.unlink()

        released = staging_leases.dismiss_review_lease(
            db_session, inbox_item_id=inbox.id
        )

        assert released is True
        assert db_session.get(StagingLease, lease.id) is None

    def test_review_lease_survives_a_staging_directory_it_cannot_read(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Cannot tell" is not "gone", and only one of them may drop the receipt.

        An unreadable staging path — a permission change, an unmounted volume — is
        the one case where the matcher genuinely does not know whether the file is
        still ours. Releasing the lease there would abandon bytes nothing will
        ever clean up, so the receipt is kept and the dismissal reports failure.
        """
        user = build_user(db_session, "lease-user")
        inbox = _inbox(db_session, user)
        staged = tmp_path / "inaccessible.3mf"
        staged.write_bytes(b"staged")
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=inbox.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=6,
            sha256="c" * 64,
        )
        db_session.commit()

        def deny_lstat(_path: Path) -> object:
            raise PermissionError("staging unavailable")

        monkeypatch.setattr(Path, "lstat", deny_lstat)

        released = staging_leases.dismiss_review_lease(
            db_session, inbox_item_id=inbox.id
        )

        assert released is False
        assert db_session.get(StagingLease, lease.id) is not None


class TestDowngrade:
    def test_fc15_round_trips_without_losing_job_lease_data(
        self, tmp_path: Path
    ) -> None:
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
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


def _mark_capture_slot(path: Path, slot_id: str) -> None:
    """Stamp the slot marker production stamps, where the platform has xattrs.

    `prepare_capture_slot_staging` sets this attribute before any request byte is
    written, so a spool that lacks it is — correctly — refused as a replacement.
    A fixture that wrote the file by hand and skipped the marker therefore built
    a state production never produces, and the test that used it passed on macOS
    (where `os.getxattr` does not exist at all, so the check is skipped) while
    failing on Linux (where a missing attribute raises `ENODATA` and the matcher
    fails closed). Stamping it here keeps the arrange step faithful on both.
    """

    setxattr = getattr(os, "setxattr", None)
    if setxattr is None:
        return
    try:
        setxattr(path, staging_leases._CAPTURE_MARKER, slot_id.encode("ascii"))
    except OSError:
        # A filesystem without xattr support; the device/inode proof is the
        # fallback there, which is its own test below.
        pass


def _lease_for(path: Path, **overrides) -> StagingLease:
    info = path.lstat()
    fields = {
        "id": "lease-1",
        "path": str(path),
        "size_bytes": info.st_size,
        "sha256": "a" * 64,
        "device": info.st_dev,
        "inode": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "expires_at": utcnow(),
    }
    fields.update(overrides)
    return StagingLease(**fields)


class TestMatchingPath:
    def test_matches_the_file_the_lease_was_taken_on(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")

        assert staging_leases._matching_path(_lease_for(path)) == path

    def test_refuses_a_lease_with_no_identity_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")

        # A lease from before identity was recorded proves nothing about the
        # file at its path, so it is never a licence to unlink.
        assert staging_leases._matching_path(_lease_for(path, device=None)) is None

    def test_refuses_a_file_that_is_gone(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_path_that_is_now_a_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()
        path.mkdir()

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_path_that_is_now_a_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "somebodys-library.stl"
        target.write_bytes(b"not ours")
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        path.unlink()
        path.symlink_to(target)

        # This is the whole reason `lstat` is used rather than `stat`: following
        # the link would delete the user's file.
        assert staging_leases._matching_path(lease) is None
        assert target.exists()

    def test_refuses_a_replacement_at_the_same_path(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path)
        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(b"payload")
        replacement.replace(path)

        assert staging_leases._matching_path(lease) is None

    def test_refuses_a_file_whose_size_changed(self, tmp_path: Path) -> None:
        path = tmp_path / "staged.bin"
        path.write_bytes(b"payload")
        lease = _lease_for(path, size_bytes=999)

        # An ordinary lease is taken on a finished file; a size change means it
        # is not the file the lease describes.
        assert staging_leases._matching_path(lease) is None


class TestMatchingCaptureStagingPath:
    @pytest.fixture
    def spool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "staging_dir", tmp_path)

        def build(slot_id: str = "slot-1", data: bytes = b"partial") -> Path:
            path = staging_leases.capture_slot_staging_path(slot_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            _mark_capture_slot(path, slot_id)
            return path

        return build

    def _lease(self, path: Path, slot_id: str = "slot-1", **overrides) -> StagingLease:
        return _lease_for(path, capture_upload_slot_id=slot_id, **overrides)

    def test_matches_a_spool_still_being_written(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.write_bytes(b"more bytes than before")

        # Size and ctime legitimately change while the upload is in flight.
        assert staging_leases._matching_capture_staging_path(lease) == path

    def test_refuses_a_lease_naming_no_slot(self, spool) -> None:
        path = spool()

        assert (
            staging_leases._matching_capture_staging_path(
                _lease_for(path, capture_upload_slot_id=None)
            )
            is None
        )

    def test_refuses_a_lease_with_no_inode_recorded(self, spool) -> None:
        path = spool()

        assert (
            staging_leases._matching_capture_staging_path(self._lease(path, inode=None))
            is None
        )

    def test_refuses_a_path_that_is_not_the_slots_own(
        self, spool, tmp_path: Path
    ) -> None:
        spool()
        elsewhere = tmp_path / "elsewhere.bin"
        elsewhere.write_bytes(b"partial")

        # The path is derived from the slot id, so a lease pointing anywhere
        # else was not written by this slot.
        assert (
            staging_leases._matching_capture_staging_path(
                _lease_for(elsewhere, capture_upload_slot_id="slot-1")
            )
            is None
        )

    def test_refuses_a_spool_that_is_gone(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_capture_path_that_is_now_a_directory(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()
        path.mkdir()

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_replacement_at_the_slots_path(self, spool) -> None:
        path = spool()
        lease = self._lease(path)
        path.unlink()
        path.write_bytes(b"somebody else's bytes")

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_spool_with_a_different_recorded_inode(self, spool) -> None:
        path = spool()
        lease = self._lease(path, inode=path.stat().st_ino + 1)

        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_refuses_a_spool_whose_marker_names_another_slot(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)
        monkeypatch.setattr(
            staging_leases.os,
            "getxattr",
            lambda _p, _n: b"a-different-slot",
            raising=False,
        )

        # The marker survives writes to the owned inode but not a replacement,
        # so a mismatch means this file is not ours even if the inode matches.
        assert staging_leases._matching_capture_staging_path(lease) is None

    def test_accepts_a_spool_whose_marker_names_it(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)
        monkeypatch.setattr(
            staging_leases.os, "getxattr", lambda _p, _n: b"slot-1", raising=False
        )

        assert staging_leases._matching_capture_staging_path(lease) == path

    def test_falls_back_to_inode_proof_when_xattr_api_is_absent(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)
        monkeypatch.delattr(staging_leases.os, "getxattr")

        assert staging_leases._matching_capture_staging_path(lease) == path

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param(getattr(errno, "ENOTSUP", 95), id="not-supported"),
            pytest.param(getattr(errno, "ENOSYS", 38), id="not-implemented"),
        ],
    )
    def test_falls_back_to_inode_proof_on_a_filesystem_without_xattrs(
        self, spool, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        path = spool()
        lease = self._lease(path)

        def unsupported(_path: object, _name: object) -> bytes:
            raise OSError(code, "not supported")

        monkeypatch.setattr(staging_leases.os, "getxattr", unsupported, raising=False)

        # No xattrs is not a reason to stop cleaning up; device and inode still
        # prove ownership.
        assert staging_leases._matching_capture_staging_path(lease) == path

    def test_refuses_when_a_supported_marker_is_missing(
        self, spool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = spool()
        lease = self._lease(path)

        def missing(_path: object, _name: object) -> bytes:
            raise OSError(errno.ENODATA, "no such attribute")

        monkeypatch.setattr(staging_leases.os, "getxattr", missing, raising=False)

        # Fail closed: a filesystem that supports xattrs and has no marker means
        # this is a replacement, not an owned partial.
        assert staging_leases._matching_capture_staging_path(lease) is None
