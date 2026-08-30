"""Durable, identity-checked ownership for files in the staging directory.

The lease row is the authority for an exact staged filesystem object.  This
module intentionally never walks directories: cleanup can only unlink the
recorded path after its device/inode/ctime/size still match the receipt.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterator

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    ModelSourceCover,
    StagingLease,
)
from app.services.storage_backend import CreationReceipt, StorageBackend

_LEASE_TABLE = getattr(StagingLease, "__table__")  # noqa: B009
_CAPTURE_MARKER = b"user.printstash.capture-slot"
_logger = logging.getLogger(__name__)


def capture_slot_staging_path(slot_id: str) -> Path:
    """Return the process-local, deterministic upload staging path.

    The path is deliberately separate from the StorageBackend destination key:
    a remote backend still needs a local spool while bytes are validated, but
    cleanup must not inspect or branch on the backend implementation.
    """
    return settings.incoming_dir / "capture-slots" / f"{slot_id}.upload"


def _quarantine_entry_path(path: Path, receipt_id: str) -> Path | None:
    """Return the deterministic private receipt path, if its id is safe."""
    if not receipt_id or any(
        character
        not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
        for character in receipt_id
    ):
        return None
    return path.parent / ".printstash-staging-quarantine" / f"{receipt_id}.entry"


def _entry_present(path: Path | None) -> bool:
    """Return whether a directory entry exists, including a symlink or error."""
    if path is None:
        return False
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


class StagingLeaseError(RuntimeError):
    """A requested lease transition cannot safely be applied."""


class StagingLeaseNotFoundError(StagingLeaseError):
    """No lease exists for the requested owner."""


class StagingLeaseAmbiguousError(StagingLeaseError):
    """Multiple leases exist for an owner that requires exactly one."""


class StagingCapacityExceeded(StagingLeaseError):
    """Staging capacity could not be proven available."""


def _matching_path(lease: StagingLease) -> Path | None:
    """Return the recorded path only when it is still the received file."""
    if None in (lease.device, lease.inode, lease.ctime_ns):
        return None
    path = Path(lease.path)
    try:
        current = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(current.st_mode):
        return None
    if (current.st_dev, current.st_ino, current.st_ctime_ns, current.st_size) != (
        lease.device,
        lease.inode,
        lease.ctime_ns,
        lease.size_bytes,
    ):
        return None
    return path


def _matching_capture_staging_path(lease: StagingLease) -> Path | None:
    """Return an owned capture spool, including a process-kill partial file.

    Capture uploads are written into the recorded inode, so its size and ctime
    legitimately change while the request is in flight.  Device/inode still
    prevents a replacement or foreign file at the same deterministic path from
    being removed.  The declared size is an upper bound for a partial spool.
    """
    slot_id = lease.capture_upload_slot_id or lease.capture_upload_slot_origin_id
    if slot_id is None or lease.device is None or lease.inode is None:
        return None
    path = capture_slot_staging_path(slot_id)
    if lease.path != str(path):
        return None
    try:
        current = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(current.st_mode):
        return None
    if current.st_dev != lease.device or current.st_ino != lease.inode:
        return None
    # Linux filesystems retain this marker across writes to the owned inode,
    # but a path replacement (including inode-number reuse) does not.  On a
    # filesystem without xattrs the device/inode proof remains the fallback;
    # the deterministic path is still never recursively scanned.
    try:
        marker = os.getxattr(path, _CAPTURE_MARKER)
    except AttributeError:
        marker = None
    except OSError as exc:
        if exc.errno in {
            getattr(errno, "ENOTSUP", 95),
            getattr(errno, "EOPNOTSUPP", 95),
            getattr(errno, "ENOSYS", 38),
        }:
            marker = None
        else:
            # A supported xattr namespace with a missing marker means this is
            # a replacement, not an owned partial. Preserve it fail-closed.
            return None
    if marker is not None:
        expected = (slot_id or "").encode("ascii", "ignore")
        if marker != expected:
            return None
    return path


def _quarantine_owned_file(
    path: Path,
    *,
    receipt_id: str,
    device: int,
    inode: int,
    ctime_ns: int | None = None,
    size_bytes: int | None = None,
    marker: bytes | None = None,
) -> bool:
    """Atomically quarantine and remove one receipt-owned regular file.

    A path check followed by ``unlink`` can delete a replacement written in
    the check-to-unlink window.  Rename selects the directory entry atomically,
    so a writer that wins that race is moved into quarantine and then fails the
    second identity proof.  Such bytes are restored when the original name is
    vacant, or retained under the private name when another writer occupied it.
    ``False`` always means the caller must retain its lease/accounting row.
    """

    def matches(
        info: os.stat_result, candidate: Path, *, check_ctime: bool = True
    ) -> bool:
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_dev != device or info.st_ino != inode:
            return False
        if check_ctime and ctime_ns is not None and info.st_ctime_ns != ctime_ns:
            return False
        if size_bytes is not None and info.st_size != size_bytes:
            return False
        if marker is not None:
            try:
                actual = os.getxattr(candidate, _CAPTURE_MARKER)
            except AttributeError:
                actual = marker
            except OSError as exc:
                if exc.errno in {
                    getattr(errno, "ENOTSUP", 95),
                    getattr(errno, "EOPNOTSUPP", 95),
                    getattr(errno, "ENOSYS", 38),
                }:
                    actual = marker
                else:
                    return False
            if actual != marker:
                return False
        return True

    quarantine = _quarantine_entry_path(path, receipt_id)
    if quarantine is None:
        return False
    parent_fd: int | None = None
    quarantine_fd: int | None = None
    quarantine_created = False
    moved = False
    quarantine_dir_name = ".printstash-staging-quarantine"
    quarantine_name = quarantine.name
    quarantine_dir = quarantine.parent

    def remove_empty_quarantine_dir() -> None:
        """Drop only our now-empty quarantine directory via its parent FD."""
        try:
            os.rmdir(quarantine_dir_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            # A retained/foreign entry or a transient filesystem failure keeps
            # the private directory for a later reconciliation pass.
            pass

    try:
        parent_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.mkdir(quarantine_dir_name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        except OSError:
            return False
        try:
            quarantine_dir_info = os.stat(
                quarantine_dir_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError:
            return False
        if (
            not stat.S_ISDIR(quarantine_dir_info.st_mode)
            or stat.S_IMODE(quarantine_dir_info.st_mode) != 0o700
        ):
            return False
        try:
            quarantine_fd = os.open(
                quarantine_dir_name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except OSError:
            return False
        quarantine = quarantine_dir / quarantine_name

        # A previous unlink/fsync failure leaves a durable, rediscoverable
        # entry. Re-prove and retry that entry before considering the original
        # path, which may now be absent or occupied by a replacement.
        try:
            retained = os.stat(
                quarantine_name, dir_fd=quarantine_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            retained = None
        except OSError:
            return False
        if retained is not None:
            if not matches(retained, quarantine, check_ctime=False):
                return False
            try:
                retained_final = os.stat(
                    quarantine_name, dir_fd=quarantine_fd, follow_symlinks=False
                )
                if not matches(retained_final, quarantine, check_ctime=False):
                    return False
                # If directory durability cannot be confirmed, retain the
                # entry so the next cleanup can rediscover it safely.
                os.fsync(quarantine_fd)
                os.unlink(quarantine_name, dir_fd=quarantine_fd)
                os.fsync(quarantine_fd)
                remove_empty_quarantine_dir()
            except OSError:
                return False
            return True

        try:
            before = path.lstat()
        except OSError:
            return False
        if not matches(before, path):
            return False

        # The private name is reserved before the cross-directory rename so a
        # concurrent operation can never overwrite an existing receipt.
        try:
            fd = os.open(
                quarantine_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=quarantine_fd,
            )
            os.close(fd)
            quarantine_created = True
            os.rename(
                path.name,
                quarantine_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=quarantine_fd,
            )
            moved = True
        except FileNotFoundError:
            return False
        except OSError:
            return False

        moved_info = os.stat(
            quarantine_name, dir_fd=quarantine_fd, follow_symlinks=False
        )
        # POSIX rename updates ctime on some filesystems. Device/inode/size
        # still prove that the quarantined entry is the originally selected
        # regular file.
        if not matches(moved_info, quarantine, check_ctime=False):
            # Restore through a no-replace hard link. If a writer filled the
            # original name, leave both entries intact for reconciliation.
            try:
                os.link(
                    quarantine_name,
                    path.name,
                    src_dir_fd=quarantine_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            except OSError:
                pass
            else:
                try:
                    os.unlink(quarantine_name, dir_fd=quarantine_fd)
                    quarantine_created = False
                except OSError:
                    pass
            return False

        # Re-proof the private entry immediately before deletion. The original
        # path is no longer used, so a replacement there cannot be unlinked.
        final = os.stat(quarantine_name, dir_fd=quarantine_fd, follow_symlinks=False)
        if not matches(final, quarantine, check_ctime=False):
            return False
        os.fsync(quarantine_fd)
        os.unlink(quarantine_name, dir_fd=quarantine_fd)
        quarantine_created = False
        os.fsync(quarantine_fd)
        remove_empty_quarantine_dir()
        return True
    except OSError:
        return False
    finally:
        if parent_fd is not None:
            if quarantine_created and not moved:
                try:
                    if quarantine_fd is not None:
                        os.unlink(quarantine_name, dir_fd=quarantine_fd)
                    quarantine_created = False
                except OSError:
                    pass
            if quarantine_created:
                _logger.warning(
                    "staging quarantine retained for reconciliation",
                    extra={"path": str(path), "quarantine": quarantine_name},
                )
            if quarantine_fd is not None:
                os.close(quarantine_fd)
            os.close(parent_fd)


def _capture_slot_lease(session: Session, slot_id: str) -> StagingLease:
    return _one_owner_lease(session, capture_upload_slot_id=slot_id)


def prepare_capture_slot_staging(session: Session, *, slot_id: str) -> Path:
    """Reserve an exact local spool before accepting upload bytes.

    A slot lease is committed with the placeholder inode before any request
    bytes are written.  A retry can safely remove only that same inode and
    replace it with a newly recorded placeholder; a collision is left intact.
    """
    lease = _capture_slot_lease(session, slot_id)
    path = capture_slot_staging_path(slot_id)
    if lease.path != str(path):
        raise StagingLeaseError("capture_upload_staging_path_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)

    lease_slot_id = lease.capture_upload_slot_id or lease.capture_upload_slot_origin_id
    quarantine = _quarantine_entry_path(path, lease.id)
    if _entry_present(path) or _entry_present(quarantine):
        if (
            lease.device is None
            or lease.inode is None
            or lease_slot_id is None
            or not _quarantine_owned_file(
                path,
                receipt_id=lease.id,
                device=lease.device,
                inode=lease.inode,
                marker=lease_slot_id.encode("ascii"),
            )
        ):
            raise StagingLeaseError("capture_upload_staging_collision")
        lease.device = lease.inode = lease.ctime_ns = None
        session.add(lease)
        session.flush()

    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise StagingLeaseError("capture_upload_staging_collision") from exc
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.setxattr(path, _CAPTURE_MARKER, slot_id.encode("ascii"))
    except (AttributeError, OSError):
        # xattrs are unavailable on some supported local filesystems. The
        # durable device/inode receipt remains the portable fallback.
        pass
    else:
        _fsync_directory(path.parent)
    received = path.lstat()
    lease.device = received.st_dev
    lease.inode = received.st_ino
    lease.ctime_ns = received.st_ctime_ns
    session.add(lease)
    # The ownership receipt must survive a process kill before the first byte.
    try:
        session.commit()
    except Exception:
        # No durable lease exists when this commit fails. Remove only the inode
        # created above; a concurrent replacement is left untouched.
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (
                received.st_dev,
                received.st_ino,
            ):
                path.unlink()
                _fsync_directory(path.parent)
        except OSError:
            pass
        raise
    return path


def _fsync_directory(path: Path) -> None:
    """Persist a deterministic spool directory entry without walking it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def open_capture_slot_staging(
    session: Session, *, slot_id: str, truncate: bool = True
) -> Iterator[BinaryIO]:
    """Open the exact lease-owned capture inode for request/service writes.

    The lease is committed before this function is called.  Opening with
    ``O_NOFOLLOW`` and checking both descriptor and path identity prevents a
    replacement at the deterministic name from being accepted or removed.
    ``truncate`` changes bytes only in the already-owned inode; it never
    replaces the directory entry.
    """
    lease = _capture_slot_lease(session, slot_id)
    path = capture_slot_staging_path(slot_id)
    if lease.path != str(path) or _matching_capture_staging_path(lease) is None:
        raise StagingLeaseError("capture_upload_staging_collision")
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    if truncate:
        flags |= os.O_TRUNC
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StagingLeaseError("capture_upload_staging_unavailable") from exc
    try:
        opened = os.fstat(fd)
        if opened.st_dev != lease.device or opened.st_ino != lease.inode:
            raise StagingLeaseError("capture_upload_staging_collision")
        with os.fdopen(fd, "wb") as target:
            fd = -1
            yield target
            target.flush()
            os.fsync(target.fileno())
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != lease.device
            or current.st_ino != lease.inode
        ):
            raise StagingLeaseError("capture_upload_staging_collision")
    finally:
        if fd >= 0:
            os.close(fd)


def stage_capture_slot_stream(
    session: Session,
    *,
    slot_id: str,
    stream: BinaryIO,
    max_bytes: int,
) -> tuple[Path, int, str]:
    """Write one synchronous stream into the deterministic slot spool."""
    import hashlib

    from printstash_core.files import UploadTooLarge

    digest = hashlib.sha256()
    received = 0
    with open_capture_slot_staging(session, slot_id=slot_id) as target:
        while chunk := stream.read(1024 * 1024):
            received += len(chunk)
            if received > max_bytes:
                raise UploadTooLarge("upload_too_large")
            target.write(chunk)
            digest.update(chunk)
    return capture_slot_staging_path(slot_id), received, digest.hexdigest()


def remove_capture_slot_staging(
    session: Session, *, slot_id: str | None = None, lease: StagingLease | None = None
) -> bool:
    """Unlink a capture spool only when its recorded inode still owns the path."""
    if lease is None:
        if slot_id is None:
            raise ValueError("slot_id or lease is required")
        lease = _capture_slot_lease(session, slot_id)
    slot_id = (
        slot_id or lease.capture_upload_slot_id or lease.capture_upload_slot_origin_id
    )
    path = capture_slot_staging_path(slot_id) if slot_id is not None else None
    quarantine = _quarantine_entry_path(path, lease.id) if path is not None else None
    if lease.device is not None and lease.inode is not None and path is not None:
        if _quarantine_owned_file(
            path,
            receipt_id=lease.id,
            device=lease.device,
            inode=lease.inode,
            marker=slot_id.encode("ascii") if slot_id is not None else None,
        ):
            lease.device = lease.inode = lease.ctime_ns = None
            session.add(lease)
            session.flush()
            try:
                lease.path and Path(lease.path).parent.rmdir()
            except OSError:
                pass
            return True
        if _entry_present(quarantine) or _entry_present(path):
            return False
        return False
    if lease.device is None and lease.inode is None:
        # A previously successful cleanup clears the receipt identity before
        # its owner row is removed. Treat that exact, already-empty state as an
        # idempotent success, but never release an identity-bearing lease just
        # because its path is absent or inaccessible.
        if path is None:
            return True
        if not _entry_present(path) and not _entry_present(quarantine):
            return True
    return False


def reconcile_capture_staging(session: Session) -> int:
    """Remove exact capture spools stranded by a killed process."""
    leases = list(
        session.exec(
            select(StagingLease).where(
                (StagingLease.capture_upload_slot_id.is_not(None))
                | (StagingLease.capture_upload_slot_origin_id.is_not(None))
            )
        )
    )
    removed = 0
    for lease in leases:
        if remove_capture_slot_staging(session, lease=lease):
            removed += 1
    session.flush()
    return removed


def _one_owner_lease(
    session: Session,
    *,
    inbox_item_id: int | None = None,
    job_id: str | None = None,
    model_source_cover_id: int | None = None,
    capture_upload_slot_id: str | None = None,
) -> StagingLease:
    owners = (inbox_item_id, job_id, model_source_cover_id, capture_upload_slot_id)
    if sum(owner is not None for owner in owners) != 1:
        raise ValueError("provide exactly one lease owner")
    column, owner_id = (
        (StagingLease.inbox_item_id, inbox_item_id)
        if inbox_item_id is not None
        else (StagingLease.background_job_id, job_id)
        if job_id is not None
        else (StagingLease.model_source_cover_id, model_source_cover_id)
        if model_source_cover_id is not None
        else (StagingLease.capture_upload_slot_id, capture_upload_slot_id)
    )
    leases = list(session.exec(select(StagingLease).where(column == owner_id)))
    if not leases:
        raise StagingLeaseNotFoundError("staging lease not found")
    if len(leases) != 1:
        raise StagingLeaseAmbiguousError("staging lease ownership ambiguous")
    return leases[0]


def create_cover_lease(
    session: Session,
    *,
    model_source_cover_id: int,
    owner_user_id: int | None,
    destination_key: str,
    size_bytes: int,
    sha256: str,
    now: datetime | None = None,
) -> StagingLease:
    """Durably reserve one in-flight cover publish without assuming local storage.

    ``path`` is an opaque audit locator for this non-filesystem lease; cleanup
    never calls the staged-file unlink path for cover-owned leases.
    """
    if session.get(ModelSourceCover, model_source_cover_id) is None:
        raise StagingLeaseError("source cover does not exist")
    timestamp = now or utcnow()
    lease = StagingLease(
        id=uuid.uuid4().hex,
        path=f"cover:{model_source_cover_id}:{uuid.uuid4().hex}",
        owner_user_id=owner_user_id,
        model_source_cover_id=model_source_cover_id,
        destination_key=destination_key,
        size_bytes=size_bytes,
        sha256=sha256,
        expires_at=timestamp + timedelta(hours=settings.staging_import_lease_hours),
    )
    session.add(lease)
    session.flush()
    return lease


def create_capture_slot_lease(
    session: Session,
    *,
    slot_id: str,
    owner_user_id: int,
    destination_key: str,
    size_bytes: int,
    sha256: str,
    now: datetime | None = None,
) -> StagingLease:
    """Reserve a declared upload slot before bytes arrive.

    The path is an opaque storage locator. A later receipt makes cleanup
    backend-native, so this never assumes a local filesystem stage.
    """
    if session.get(CaptureUploadSlot, slot_id) is None:
        raise StagingLeaseError("capture upload slot does not exist")
    staging_path = capture_slot_staging_path(slot_id)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_capacity(
        session,
        owner_user_id=owner_user_id,
        size_bytes=size_bytes,
        capacity_path=settings.incoming_dir,
    )
    lease = StagingLease(
        id=uuid.uuid4().hex,
        path=str(staging_path),
        owner_user_id=owner_user_id,
        capture_upload_slot_id=slot_id,
        destination_key=destination_key,
        size_bytes=size_bytes,
        sha256=sha256,
        expires_at=(now or utcnow())
        + timedelta(days=settings.staging_review_lease_days),
    )
    session.add(lease)
    session.flush()
    return lease


def record_capture_slot_receipt(
    session: Session, *, slot_id: str, receipt: CreationReceipt
) -> None:
    lease = _one_owner_lease(session, capture_upload_slot_id=slot_id)
    lease.destination_key = receipt.key
    lease.receipt_json = json.dumps(
        {
            "key": receipt.key,
            "size": receipt.size,
            "token": receipt.token,
            "backend": receipt.backend,
            "namespace": receipt.namespace,
            "etag": receipt.etag,
            "version_id": receipt.version_id,
            "device": receipt.device,
            "inode": receipt.inode,
            "ctime_ns": receipt.ctime_ns,
        },
        sort_keys=True,
    )
    session.add(lease)
    session.flush()


def _receipt_from_json(value: str | None) -> CreationReceipt | None:
    """Decode a persisted receipt without allowing malformed state to escape."""
    try:
        raw = json.loads(value or "")
        if not isinstance(raw, dict):
            return None
        return CreationReceipt(**raw)
    except (TypeError, ValueError):
        return None


def _receipt_json(receipt: CreationReceipt) -> str:
    return json.dumps(
        {
            "key": receipt.key,
            "size": receipt.size,
            "token": receipt.token,
            "backend": receipt.backend,
            "namespace": receipt.namespace,
            "etag": receipt.etag,
            "version_id": receipt.version_id,
            "device": receipt.device,
            "inode": receipt.inode,
            "ctime_ns": receipt.ctime_ns,
        },
        sort_keys=True,
    )


def reconcile_capture_slot(
    session: Session, backend: StorageBackend, slot: CaptureUploadSlot
) -> bool:
    """Recover one capture slot after publication/DB failure.

    Slot and lease rows are committed before any object write.  If a process
    dies after publication, the exact declared key is reconciled by a backend
    that can adopt an existing object only after verifying its declared size,
    digest, and deletion identity.  A mismatched/collision object is never
    claimed or deleted.
    """
    lease = session.exec(
        select(StagingLease).where(
            (StagingLease.capture_upload_slot_id == slot.id)
            | (StagingLease.capture_upload_slot_origin_id == slot.id)
        )
    ).first()
    if lease is None or not slot.storage_key:
        return False

    receipt = _receipt_from_json(slot.receipt_json) or _receipt_from_json(
        lease.receipt_json
    )
    if receipt is not None:
        try:
            if not backend.creation_matches(receipt):
                receipt = None
        except Exception:
            raise

    if receipt is None:
        # Do not use exists() as ownership proof.  adopt_existing() is the
        # backend seam that verifies content and binds a deletion identity.
        try:
            receipt = backend.adopt_existing(
                slot.storage_key,
                expected_size=slot.size_bytes,
                expected_sha256=slot.sha256,
            )
        except (
            FileNotFoundError,
            OSError,
            NotImplementedError,
            RuntimeError,
            ValueError,
        ):
            return False

    if receipt.key != slot.storage_key or receipt.size != slot.size_bytes:
        return False
    slot.state = CaptureUploadSlotState.UPLOADED
    slot.receipt_json = _receipt_json(receipt)
    slot.uploaded_at = slot.uploaded_at or utcnow()
    slot.updated_at = utcnow()
    lease.destination_key = receipt.key
    lease.receipt_json = _receipt_json(receipt)
    session.add(slot)
    session.add(lease)
    session.flush()
    return True


def reconcile_capture_slots(session: Session, backend: StorageBackend) -> int:
    """Reconcile all durable capture intents and return recovered count."""
    recovered = 0
    slots = session.exec(select(CaptureUploadSlot)).all()
    for slot in slots:
        if slot.state == CaptureUploadSlotState.UPLOADED and slot.receipt_json:
            continue
        if reconcile_capture_slot(session, backend, slot):
            recovered += 1
    return recovered


def transfer_capture_slots_to_job(
    session: Session, *, inbox_item_id: int, job_id: str, now: datetime | None = None
) -> list[StagingLease]:
    if session.get(BackgroundJob, job_id) is None:
        raise StagingLeaseError("background job does not exist")
    slots = list(
        session.exec(
            select(CaptureUploadSlot).where(
                CaptureUploadSlot.inbox_item_id == inbox_item_id
            )
        )
    )
    leases: list[StagingLease] = []
    for slot in slots:
        lease = session.exec(
            select(StagingLease).where(
                (StagingLease.capture_upload_slot_id == slot.id)
                | (StagingLease.capture_upload_slot_origin_id == slot.id)
            )
        ).first()
        if lease is None:
            raise StagingLeaseError("capture upload slot lease missing")
        lease.capture_upload_slot_origin_id = slot.id
        lease.capture_upload_slot_id = None
        lease.background_job_id = job_id
        lease.expires_at = (now or utcnow()) + timedelta(
            hours=settings.staging_import_lease_hours
        )
        leases.append(lease)
    session.flush()
    return leases


def return_capture_slots_to_review(
    session: Session, *, inbox_item_id: int, job_id: str, now: datetime | None = None
) -> list[StagingLease]:
    """Return a capture import's slot custody from its job to browser review."""
    slots = list(
        session.exec(
            select(CaptureUploadSlot).where(
                CaptureUploadSlot.inbox_item_id == inbox_item_id
            )
        )
    )
    leases: list[tuple[CaptureUploadSlot, StagingLease]] = []
    for slot in slots:
        matches = list(
            session.exec(
                select(StagingLease).where(
                    StagingLease.capture_upload_slot_origin_id == slot.id,
                    StagingLease.background_job_id == job_id,
                )
            )
        )
        if len(matches) != 1:
            raise StagingLeaseError("capture upload slot job lease missing")
        leases.append((slot, matches[0]))
    for slot, lease in leases:
        lease.background_job_id = None
        lease.capture_upload_slot_origin_id = None
        lease.capture_upload_slot_id = slot.id
        lease.expires_at = (now or utcnow()) + timedelta(
            days=settings.staging_review_lease_days
        )
    session.flush()
    return [lease for _, lease in leases]


def return_inbox_lease_to_review(
    session: Session, *, inbox_item_id: int, job_id: str, now: datetime | None = None
) -> StagingLease:
    """Return one legacy browser-file lease from a job to its inbox review."""
    lease = _one_owner_lease(session, job_id=job_id)
    # SQLite hands a DateTime column back naive; normalize both sides once so
    # expiry validation and the renewed deadline share the same instant.
    current = ensure_utc(now or utcnow())
    if ensure_utc(lease.expires_at) <= current:
        raise StagingLeaseError("staging lease expired")
    lease.background_job_id = None
    lease.inbox_item_id = inbox_item_id
    lease.expires_at = current + timedelta(days=settings.staging_review_lease_days)
    session.flush()
    return lease


def record_cover_receipt(
    session: Session, *, lease: StagingLease, receipt: CreationReceipt
) -> None:
    if lease.model_source_cover_id is None:
        raise StagingLeaseError("lease is not cover-owned")
    lease.destination_key = receipt.key
    lease.receipt_json = json.dumps(
        {
            "key": receipt.key,
            "size": receipt.size,
            "token": receipt.token,
            "backend": receipt.backend,
            "namespace": receipt.namespace,
            "etag": receipt.etag,
            "version_id": receipt.version_id,
            "device": receipt.device,
            "inode": receipt.inode,
            "ctime_ns": receipt.ctime_ns,
        },
        sort_keys=True,
    )
    session.add(lease)
    session.flush()


def release_cover_lease(session: Session, *, model_source_cover_id: int) -> None:
    lease = _one_owner_lease(session, model_source_cover_id=model_source_cover_id)
    session.delete(lease)
    session.flush()


def _ensure_capacity(
    session: Session,
    *,
    owner_user_id: int | None,
    size_bytes: int,
    capacity_path: Path,
) -> None:
    """Reject when capacity is exceeded *or cannot be measured*."""
    try:
        count, used = session.exec(
            select(
                func.count(_LEASE_TABLE.c.id),
                func.coalesce(func.sum(_LEASE_TABLE.c.size_bytes), 0),
            )
        ).one()
        owner_count = session.exec(
            select(func.count(_LEASE_TABLE.c.id)).where(
                StagingLease.owner_user_id == owner_user_id
            )
        ).one()
        filesystem = os.statvfs(capacity_path)
        free = filesystem.f_bavail * filesystem.f_frsize
    except Exception as exc:  # capacity uncertainty must never be treated as room
        raise StagingCapacityExceeded("staging_capacity_unavailable") from exc
    if (
        int(count) >= settings.staging_max_pending
        or int(owner_count) >= settings.staging_max_active_per_user
        or int(used) + size_bytes > settings.staging_max_gb * 1024**3
        or free < settings.staging_min_free_gb * 1024**3
    ):
        raise StagingCapacityExceeded("staging_capacity_exceeded")


def create_review_lease(
    session: Session,
    *,
    inbox_item_id: int,
    owner_user_id: int | None,
    path: Path,
    size_bytes: int,
    sha256: str,
    now: datetime | None = None,
) -> StagingLease:
    """Create the 30-day review lease after capacity and identity checks."""
    if session.get(InboxItem, inbox_item_id) is None:
        raise StagingLeaseError("inbox item does not exist")
    try:
        received = path.lstat()
    except OSError as exc:
        raise StagingLeaseError("staged path is unavailable") from exc
    if not stat.S_ISREG(received.st_mode) or received.st_size != size_bytes:
        raise StagingLeaseError("staged path identity does not match receipt")
    _ensure_capacity(
        session,
        owner_user_id=owner_user_id,
        size_bytes=size_bytes,
        capacity_path=path.parent,
    )
    timestamp = now or utcnow()
    lease = StagingLease(
        id=uuid.uuid4().hex,
        path=str(path),
        owner_user_id=owner_user_id,
        inbox_item_id=inbox_item_id,
        size_bytes=size_bytes,
        sha256=sha256,
        device=received.st_dev,
        inode=received.st_ino,
        ctime_ns=received.st_ctime_ns,
        expires_at=timestamp + timedelta(days=settings.staging_review_lease_days),
    )
    session.add(lease)
    session.flush()
    return lease


def transfer_inbox_to_job(
    session: Session, *, inbox_item_id: int, job_id: str, now: datetime | None = None
) -> StagingLease:
    """Atomically replace an inbox owner with a 24-hour job owner."""
    lease = _one_owner_lease(session, inbox_item_id=inbox_item_id)
    if session.get(BackgroundJob, job_id) is None:
        raise StagingLeaseError("background job does not exist")
    timestamp = now or utcnow()
    # The XOR check is deferred only by this single UPDATE at flush time; no
    # intermediate committed state has zero or two owners.
    lease.inbox_item_id = None
    lease.background_job_id = job_id
    lease.expires_at = timestamp + timedelta(hours=settings.staging_import_lease_hours)
    session.flush()
    return lease


def renew_review_lease(
    session: Session, *, inbox_item_id: int, now: datetime | None = None
) -> StagingLease:
    lease = _one_owner_lease(session, inbox_item_id=inbox_item_id)
    lease.expires_at = (now or utcnow()) + timedelta(
        days=settings.staging_review_lease_days
    )
    session.flush()
    return lease


def renew_job_lease(
    session: Session, *, job_id: str, now: datetime | None = None
) -> StagingLease:
    leases = list(
        session.exec(
            select(StagingLease).where(StagingLease.background_job_id == job_id)
        )
    )
    if not leases:
        raise StagingLeaseError("expected at least one staging lease for job")
    for lease in leases:
        lease.expires_at = (now or utcnow()) + timedelta(
            hours=settings.staging_import_lease_hours
        )
    lease = leases[0]
    session.flush()
    return lease


def dismiss_review_lease(session: Session, *, inbox_item_id: int) -> bool:
    """Forget a review lease, unlinking only an exact identity match.

    An already-missing path releases stale accounting idempotently. A replaced
    or otherwise uncertain path remains leased because it is not safe to unlink.
    """
    lease = _one_owner_lease(session, inbox_item_id=inbox_item_id)
    path = Path(lease.path)
    quarantine = _quarantine_entry_path(path, lease.id)
    if lease.device is not None and lease.inode is not None:
        if _quarantine_owned_file(
            path,
            receipt_id=lease.id,
            device=lease.device,
            inode=lease.inode,
            ctime_ns=lease.ctime_ns,
            size_bytes=lease.size_bytes,
        ):
            session.delete(lease)
            session.flush()
            return True
        if _entry_present(quarantine) or _entry_present(path):
            return False
    try:
        path.lstat()
    except FileNotFoundError:
        # There is no reachable object left at the exact recorded name and no
        # retained quarantine entry, so releasing stale accounting is safe.
        session.delete(lease)
        session.flush()
        return True
    except OSError:
        return False
    return False


def dismiss_capture_slot_leases(session: Session, *, inbox_item_id: int) -> bool:
    """Release pre-import slot leases by their canonical capture-slot owner id."""
    slot_ids = session.exec(
        select(CaptureUploadSlot.id).where(
            CaptureUploadSlot.inbox_item_id == inbox_item_id
        )
    ).all()
    if not slot_ids:
        return False
    leases = list(
        session.exec(
            select(StagingLease).where(
                StagingLease.capture_upload_slot_id.in_(slot_ids)  # type: ignore[attr-defined]
            )
        )
    )
    if not leases:
        raise StagingLeaseError("capture upload slot lease missing")
    for lease in leases:
        session.delete(lease)
    session.flush()
    return True


def prune_expired(
    session: Session,
    *,
    now: datetime | None = None,
    backend: StorageBackend | None = None,
) -> tuple[int, int]:
    """Remove expired rows; unlink only exact files. Returns (rows, files)."""
    timestamp = now or utcnow()
    rows = list(
        session.exec(select(StagingLease).where(StagingLease.expires_at <= timestamp))
    )
    removed = unlinked = 0
    for lease in rows:
        if lease.model_source_cover_id is not None:
            # A cover lease never represents a local path. Reconcile its
            # backend-native publication first; if no bytes were published,
            # the reconciler removes the broken cover row and stale proof too.
            # A mismatched or unavailable object remains leased for retry —
            # expiry must never become an unverified delete.
            from app.services import source_covers
            from app.services.storage_backend import get_backend

            if source_covers.expire_pending(
                session,
                backend or get_backend(),
                lease=lease,
            ):
                removed += 1
            continue
        path = Path(lease.path)
        quarantine = _quarantine_entry_path(path, lease.id)
        if lease.device is not None and lease.inode is not None:
            if _quarantine_owned_file(
                path,
                receipt_id=lease.id,
                device=lease.device,
                inode=lease.inode,
                ctime_ns=lease.ctime_ns,
                size_bytes=lease.size_bytes,
            ):
                unlinked += 1
                session.delete(lease)
                removed += 1
                continue
            if _entry_present(quarantine) or _entry_present(path):
                continue
        try:
            path.lstat()
        except FileNotFoundError:
            session.delete(lease)
            removed += 1
            continue
        except OSError:
            pass
        # Keep uncertain rows charged. A replaced or inaccessible pathname is
        # not evidence that this lease's bytes were safely reclaimed.
        continue
    session.flush()
    return removed, unlinked
