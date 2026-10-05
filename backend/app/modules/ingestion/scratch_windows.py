"""Receipt-owned private ingest workspaces; filesystem work never holds SQL locks."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Iterator

from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import CapacityReservation, StagingLease
from app.db.models.ingestion_scratch import (
    IngestionScratchWindow,
    ScratchWindowKind,
    ScratchWindowPhase,
)
from app.db.session import SessionFactory, get_session_factory
from app.modules.storage.capacity import CapacityManager, CapacityResource

WindowKind = ScratchWindowKind
_MARKER = ".ownership.json"
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class WindowCleanupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("batch_window_release_failed")


@dataclass(frozen=True)
class JobWindowOwner:
    job_id: str
    execution_epoch: str

    def __post_init__(self) -> None:
        if not self.job_id or not self.execution_epoch:
            raise ValueError("invalid_scratch_job_owner")


@dataclass(frozen=True)
class RequestWindowOwner:
    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("invalid_scratch_request_owner")


WindowOwner = JobWindowOwner | RequestWindowOwner


@dataclass(frozen=True)
class _Receipt:
    id: str
    directory: Path
    lock_path: Path
    parent_device: int
    parent_inode: int
    lock_device: int | None
    lock_inode: int | None
    token: str
    device: int | None
    inode: int | None
    output_name: str | None
    output_device: int | None
    output_inode: int | None
    transferred_path: str | None
    operation_id: str
    phase: ScratchWindowPhase


def _receipt(identifier: str, factory: SessionFactory) -> _Receipt | None:
    with factory.scoped_session() as session:
        row = session.get(IngestionScratchWindow, identifier)
        if row is None:
            return None
        return _Receipt(
            row.id,
            Path(row.path),
            Path(row.lock_path),
            row.parent_device,
            row.parent_inode,
            row.lock_device,
            row.lock_inode,
            row.marker_token,
            row.device,
            row.inode,
            row.output_name,
            row.output_device,
            row.output_inode,
            row.transferred_path,
            row.capacity_operation_id,
            row.phase,
        )


def _parent(receipt: _Receipt) -> int:
    fd = os.open(receipt.directory.parent, _FLAGS)
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (receipt.parent_device, receipt.parent_inode):
        os.close(fd)
        raise WindowCleanupError()
    return fd


def _directory(receipt: _Receipt, parent: int, name: str) -> int:
    fd = os.open(name, _FLAGS, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if receipt.device is not None and (info.st_dev, info.st_ino) != (
            receipt.device,
            receipt.inode,
        ):
            raise WindowCleanupError()
        try:
            marker = os.open(_MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            # Removal can crash between unlinking the marker and rmdir. Only
            # an empty directory with the previously recorded identity is safe.
            if receipt.device is not None and not os.listdir(fd):
                return fd
            raise WindowCleanupError() from None
        try:
            info = os.fstat(marker)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256:
                raise WindowCleanupError()
            content = os.read(marker, 257)
            if json.loads(content) != {"id": receipt.id, "token": receipt.token}:
                raise WindowCleanupError()
        finally:
            os.close(marker)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _remove(receipt: _Receipt, factory: SessionFactory) -> bool:
    # A lease commit can precede handoff's phase commit. Its custody already
    # protects the sealed output during that crash window.
    if receipt.output_name is not None:
        with factory.scoped_session() as session:
            if (
                session.exec(
                    select(StagingLease.id).where(
                        StagingLease.path
                        == str(receipt.directory / receipt.output_name)
                    )
                ).first()
                is not None
            ):
                return False
    parent = _parent(receipt)
    retired = receipt.id + ".retired"
    try:
        try:
            fd = _directory(receipt, parent, retired)
        except FileNotFoundError:
            try:
                fd = _directory(receipt, parent, receipt.directory.name)
            except FileNotFoundError:
                return True
            if receipt.output_name is not None:
                try:
                    output = os.stat(
                        receipt.output_name, dir_fd=fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(output.st_mode) or (
                        output.st_dev,
                        output.st_ino,
                    ) != (receipt.output_device, receipt.output_inode):
                        os.close(fd)
                        raise WindowCleanupError()
            try:
                os.rename(
                    receipt.directory.name,
                    retired,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                )
                os.fsync(parent)
            finally:
                os.close(fd)
            fd = _directory(receipt, parent, retired)
        try:
            if receipt.output_name is not None:
                try:
                    output = os.stat(
                        receipt.output_name, dir_fd=fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    pass
                else:
                    if not stat.S_ISREG(output.st_mode) or (
                        output.st_dev,
                        output.st_ino,
                    ) != (receipt.output_device, receipt.output_inode):
                        raise WindowCleanupError()
            # All partial files are within the recorded private flat workspace.
            # The marker is removed last, preserving replay after an unlink fault.
            for name in os.listdir(fd):
                if name == _MARKER:
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise WindowCleanupError()
                os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            try:
                os.unlink(_MARKER, dir_fd=fd)
            except FileNotFoundError:
                pass
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rmdir(retired, dir_fd=parent)
        os.fsync(parent)
        return True
    finally:
        os.close(parent)


def _retire(receipt: _Receipt, factory: SessionFactory) -> None:
    with factory.scoped_session() as session:
        CapacityManager.serialize_admission(session)
        row = session.get(IngestionScratchWindow, receipt.id)
        if row is None or row.marker_token != receipt.token:
            return
        claim = session.get(CapacityReservation, receipt.operation_id)
        if claim is not None:
            session.delete(claim)
        session.delete(row)
        session.commit()


def cleanup_window(
    identifier: str, *, session_factory: SessionFactory | None = None
) -> bool:
    """Replay exact cleanup; a live FD owner or uncertain identity stays charged."""
    factory = session_factory or get_session_factory()
    parent = lock = None
    try:
        receipt = _receipt(identifier, factory)
        if receipt is None:
            return True
        parent = _parent(receipt)
        try:
            lock = os.open(
                receipt.lock_path.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent
            )
        except FileNotFoundError:
            # RETIRING was committed after exact workspace removal, before
            # removing the lock. Its final SQL retirement is safely replayable.
            if receipt.phase not in (
                ScratchWindowPhase.RETIRING,
                ScratchWindowPhase.PREPARING,
            ):
                return False
            if any(
                os.path.lexists(receipt.directory.parent / name)
                for name in (receipt.directory.name, receipt.id + ".retired")
            ):
                return False
            _retire(receipt, factory)
            return True
        info = os.fstat(lock)
        if (info.st_dev, info.st_ino) != (receipt.lock_device, receipt.lock_inode):
            return False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if not _remove(receipt, factory):
            return False
        with factory.scoped_session() as session:
            row = session.get(IngestionScratchWindow, receipt.id)
            if row is None or row.marker_token != receipt.token:
                return False
            row.phase = ScratchWindowPhase.RETIRING
            session.add(row)
            session.commit()
        # The SQL receipt remains until the exact lock name is also removed.
        current = os.stat(receipt.lock_path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (
            receipt.lock_device,
            receipt.lock_inode,
        ):
            return False
        os.unlink(receipt.lock_path.name, dir_fd=parent)
        os.fsync(parent)
        _retire(receipt, factory)
        return True
    except OSError, ValueError, WindowCleanupError, SQLAlchemyError:
        return False
    finally:
        if lock is not None:
            os.close(lock)
        if parent is not None:
            os.close(parent)


@dataclass
class ScratchWindow:
    id: str
    directory: Path
    operation_id: str
    max_bytes: int
    _lock: int | None
    _factory: SessionFactory

    def claim_job(self, session: Session, owner: JobWindowOwner) -> None:
        """Associate a live prepared window in the caller's authority transaction."""
        row = session.exec(
            select(IngestionScratchWindow)
            .where(IngestionScratchWindow.id == self.id)
            .with_for_update()
        ).first()
        if (
            self._lock is None
            or row is None
            or row.path != str(self.directory)
            or row.phase != ScratchWindowPhase.OPEN
            or row.origin_job_id is not None
        ):
            raise WindowCleanupError()
        row.job_id = row.origin_job_id = owner.job_id
        row.execution_epoch = owner.execution_epoch
        session.add(row)

    def seal(self, output: Path) -> None:
        if output.parent != self.directory:
            raise ValueError("scratch_output_outside_window")
        receipt = _receipt(self.id, self._factory)
        if receipt is None:
            raise WindowCleanupError()
        parent = _parent(receipt)
        try:
            directory = _directory(receipt, parent, self.directory.name)
            try:
                info = os.stat(output.name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise WindowCleanupError()
            finally:
                os.close(directory)
        finally:
            os.close(parent)
        with self._factory.scoped_session() as session:
            row = session.get(IngestionScratchWindow, self.id)
            if row is None:
                raise WindowCleanupError()
            row.output_name, row.output_device, row.output_inode = (
                output.name,
                info.st_dev,
                info.st_ino,
            )
            row.phase = ScratchWindowPhase.SEALED
            session.add(row)
            session.commit()

    def handoff(self, output: Path) -> None:
        """Keep a durable input lease's exact file; recovery only removes empties."""
        self.seal(output)
        with self._factory.scoped_session() as session:
            lease = session.exec(
                select(StagingLease).where(StagingLease.path == str(output))
            ).first()
            row = session.get(IngestionScratchWindow, self.id)
            if (
                lease is None
                or row is None
                or (lease.device, lease.inode) != (row.output_device, row.output_inode)
            ):
                raise WindowCleanupError()
            row.transferred_path = str(output)
            row.phase = ScratchWindowPhase.TRANSFERRED
            session.add(row)
            session.commit()
        self.detach()

    def detach(self) -> None:
        """Return a staged result with durable custody; caller later releases it."""
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def close(self) -> None:
        if self._lock is None:
            return
        self.detach()
        if not cleanup_window(self.id, session_factory=self._factory):
            try:
                with self._factory.scoped_session() as session:
                    row = session.get(IngestionScratchWindow, self.id)
                    if row is not None and row.phase in (
                        ScratchWindowPhase.OPEN,
                        ScratchWindowPhase.SEALED,
                        ScratchWindowPhase.RELEASE_PENDING,
                    ):
                        row.phase = ScratchWindowPhase.RELEASE_PENDING
                        row.available_at = utcnow()
                        session.add(row)
                        session.commit()
            except SQLAlchemyError as exc:
                raise WindowCleanupError() from exc
            raise WindowCleanupError()


def _recover_prior_windows(owner: JobWindowOwner, factory: SessionFactory) -> None:
    """An execution cannot fetch again while its previous disposable bytes remain."""
    with factory.scoped_session() as session:
        rows = session.exec(
            select(IngestionScratchWindow)
            .where(
                IngestionScratchWindow.origin_job_id == owner.job_id,
                or_(
                    col(IngestionScratchWindow.execution_epoch)
                    != owner.execution_epoch,
                    col(IngestionScratchWindow.phase)
                    == ScratchWindowPhase.RELEASE_PENDING,
                ),
            )
            .order_by(
                col(IngestionScratchWindow.created_at), col(IngestionScratchWindow.id)
            )
            .limit(65)
        ).all()
    if len(rows) > 64:
        raise WindowCleanupError()
    for row in rows:
        # Durable input custody may precede handoff's phase commit. It is valid
        # retry input, not disposable debris that a new attempt must remove.
        if row.output_name is not None:
            with factory.scoped_session() as session:
                lease = session.exec(
                    select(StagingLease).where(
                        StagingLease.path == str(Path(row.path) / row.output_name)
                    )
                ).first()
                if lease is not None and (lease.device, lease.inode) == (
                    row.output_device,
                    row.output_inode,
                ):
                    continue
        if not cleanup_window(row.id, session_factory=factory):
            raise WindowCleanupError()


def recover_prior_windows(owner: JobWindowOwner, factory: SessionFactory) -> None:
    try:
        _recover_prior_windows(owner, factory)
    except SQLAlchemyError as exc:
        raise WindowCleanupError() from exc


def create_window(
    *,
    kind: WindowKind,
    max_bytes: int,
    owner: WindowOwner | None = None,
    session_factory: SessionFactory | None = None,
) -> ScratchWindow:
    if not isinstance(kind, WindowKind) or type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("invalid_scratch_window")
    if owner is not None and not isinstance(
        owner, (JobWindowOwner, RequestWindowOwner)
    ):
        raise ValueError("invalid_scratch_owner")
    factory = session_factory or get_session_factory()
    if isinstance(owner, JobWindowOwner):
        recover_prior_windows(owner, factory)
    identifier, token = uuid.uuid4().hex, uuid.uuid4().hex
    parent = settings.incoming_dir / "scratch-windows"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise WindowCleanupError()
    lock_path = parent / (identifier + ".lock")
    lock: int | None = None
    path = parent / identifier
    operation_id = "ingestion-scratch:" + identifier
    registered = False
    try:
        with factory.scoped_session() as session:
            row = IngestionScratchWindow(
                id=identifier,
                path=str(path),
                lock_path=str(lock_path),
                parent_device=parent_info.st_dev,
                parent_inode=parent_info.st_ino,
                lock_device=None,
                lock_inode=None,
                marker_token=token,
                kind=kind,
                phase=ScratchWindowPhase.PREPARING,
                job_id=owner.job_id if isinstance(owner, JobWindowOwner) else None,
                origin_job_id=owner.job_id
                if isinstance(owner, JobWindowOwner)
                else None,
                execution_epoch=owner.execution_epoch
                if isinstance(owner, JobWindowOwner)
                else None,
                request_token=owner.token
                if isinstance(owner, RequestWindowOwner)
                else token,
                capacity_operation_id=operation_id,
                max_bytes=max_bytes,
                available_at=utcnow() + timedelta(hours=1),
            )
            session.add(row)
            session.commit()
        registered = True
        lock = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        lock_info = os.fstat(lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with factory.scoped_session() as session:
            row = session.get(IngestionScratchWindow, identifier)
            if row is None:
                raise WindowCleanupError()
            row.lock_device, row.lock_inode = lock_info.st_dev, lock_info.st_ino
            session.add(row)
            session.commit()
        path.mkdir(mode=0o700)
        fd = os.open(path, _FLAGS)
        try:
            info = os.fstat(fd)
            marker = os.open(
                _MARKER,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            try:
                content = json.dumps({"id": identifier, "token": token}).encode()
                if os.write(marker, content) != len(content):
                    raise WindowCleanupError()
                os.fsync(marker)
            finally:
                os.close(marker)
            os.fsync(fd)
            with factory.scoped_session() as session:
                row = session.get(IngestionScratchWindow, identifier)
                if row is None:
                    raise WindowCleanupError()
                row.device, row.inode = info.st_dev, info.st_ino
                row.phase = ScratchWindowPhase.OPEN
                session.add(row)
                session.commit()
        finally:
            os.close(fd)
        parent_fd = os.open(parent, _FLAGS)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        # No payload is admitted before both physical proofs are committed.
        # Unprovable PREPARING debris is empty and holds no durable credit.
        CapacityManager(factory).reserve(
            operation_id,
            [
                CapacityResource.for_path(
                    parent, max_bytes, role="ingestion scratch window"
                )
            ],
            durable=True,
        )
        return ScratchWindow(identifier, path, operation_id, max_bytes, lock, factory)
    except BaseException:
        if lock is not None:
            os.close(lock)
        if registered:
            cleanup_window(identifier, session_factory=factory)
        else:
            # No filesystem object is created before the SQL receipt.
            pass
        raise


@contextmanager
def open_window(
    *,
    kind: WindowKind,
    max_bytes: int,
    owner: WindowOwner | None = None,
    session_factory: SessionFactory | None = None,
) -> Iterator[ScratchWindow]:
    window = create_window(
        kind=kind, max_bytes=max_bytes, owner=owner, session_factory=session_factory
    )
    try:
        yield window
    finally:
        primary = sys.exception()
        try:
            window.close()
        except Exception as exc:
            if primary is not None:
                primary.add_note(f"scratch cleanup failed: {exc}")
            elif isinstance(exc, WindowCleanupError):
                raise
            else:
                raise WindowCleanupError() from exc


class ReleaseDisposition(StrEnum):
    NOT_OWNED = "not_owned"
    DEFERRED = "deferred"
    TRANSFERRED = "transferred"
    RELEASED = "released"
    UNCERTAIN = "uncertain"


def release_path(
    path: Path, *, session_factory: SessionFactory | None = None
) -> ReleaseDisposition:
    """Only the containing exact workspace can release its durable claim."""
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        identifier = session.exec(
            select(IngestionScratchWindow.id).where(
                IngestionScratchWindow.path == str(path.parent)
            )
        ).first()
    if identifier is None:
        return ReleaseDisposition.NOT_OWNED
    receipt = _receipt(identifier, factory)
    if receipt is None:
        return ReleaseDisposition.RELEASED
    if receipt.output_name != path.name:
        return ReleaseDisposition.UNCERTAIN
    if receipt.output_name is not None:
        with factory.scoped_session() as session:
            if (
                session.exec(
                    select(StagingLease.id).where(StagingLease.path == str(path))
                ).first()
                is not None
            ):
                return ReleaseDisposition.TRANSFERRED
    fd = None
    try:
        fd = os.open(receipt.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (receipt.lock_device, receipt.lock_inode):
            return ReleaseDisposition.UNCERTAIN
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return ReleaseDisposition.DEFERRED
    except FileNotFoundError:
        pass
    except OSError:
        return ReleaseDisposition.UNCERTAIN
    finally:
        if fd is not None:
            os.close(fd)
    return (
        ReleaseDisposition.RELEASED
        if cleanup_window(identifier, session_factory=factory)
        else ReleaseDisposition.UNCERTAIN
    )


def handoff_path(path: Path, *, session_factory: SessionFactory | None = None) -> None:
    """Transfer a detached download into an already committed input lease."""
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        row = session.exec(
            select(IngestionScratchWindow).where(
                IngestionScratchWindow.path == str(path.parent)
            )
        ).first()
        if row is None:
            return
        lease = session.exec(
            select(StagingLease).where(StagingLease.path == str(path))
        ).first()
        if (
            row.output_name != path.name
            or lease is None
            or (lease.device, lease.inode)
            != (
                row.output_device,
                row.output_inode,
            )
        ):
            raise WindowCleanupError()
        row.phase = ScratchWindowPhase.TRANSFERRED
        row.transferred_path = str(path)
        session.add(row)
        session.commit()
