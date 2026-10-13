"""Real application roles for interrupted scratch custody and Job recovery.

No producer or transport is replaced. The stall role owns a real workspace,
keeps a partial output open, and lets a real cleanup Job encounter its live FD.
Only the parent's SIGKILL interrupts that writer.
"""

from __future__ import annotations

import faulthandler
import hashlib
import os
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import ensure_dirs
from app.db.models import CapacityReservation, Job, JobKind, JobState
from app.db.models.ingestion_scratch import IngestionScratchWindow
from app.db.session import get_session_factory
from app.modules.ingestion.scratch_windows import (
    RequestWindowOwner,
    WindowKind,
    open_window,
)
from app.modules.work import service
from tests.fakes.job_engine_process import _diagnostics, _emit, _set_up

_DEADLINE_S = 70
_PARTIAL = b"unfinished ingestion scratch chunk\n" * 128


def _due(identifier: str) -> None:
    """Arrange an expired receipt without replacing the application's clock."""
    with get_session_factory().scoped_session() as session:
        row = session.get(IngestionScratchWindow, identifier)
        assert row is not None
        row.available_at = datetime(2000, 1, 1, tzinfo=UTC)
        session.add(row)
        session.commit()


def _completed(identifier: str) -> list[str]:
    with get_session_factory().scoped_session() as session:
        return list(
            session.exec(
                select(Job.id).where(
                    Job.kind == JobKind.INGESTION_SCRATCH_CLEANUP,
                    Job.subject_key == f"scratch_window/{identifier}",
                    Job.state == JobState.COMPLETED,
                )
            ).all()
        )


def stall() -> None:
    from app.main import app

    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        with open_window(
            kind=WindowKind.LOCAL_COPY,
            max_bytes=1024 * 1024,
            owner=RequestWindowOwner(uuid.uuid4().hex),
        ) as window:
            partial = window.directory / "unfinished.part"
            fd = os.open(
                partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as output:
                output.write(_PARTIAL)
                output.flush()
                os.fsync(output.fileno())
                _due(window.id)
                service.nudge(JobKind.INGESTION_SCRATCH_CLEANUP)
                deadline = time.monotonic() + _DEADLINE_S
                while not (completed := _completed(window.id)):
                    assert time.monotonic() < deadline, _diagnostics()
                    time.sleep(0.1)
                # A cleanup execution encountered the real live flock and left
                # both the unfinished bytes and their durable charge intact.
                assert partial.read_bytes() == _PARTIAL
                with get_session_factory().scoped_session() as session:
                    row = session.get(IngestionScratchWindow, window.id)
                    assert row is not None and row.output_name is None
                    assert (
                        session.get(CapacityReservation, window.operation_id)
                        is not None
                    )
                    lock_path = row.lock_path
                _due(window.id)
                _emit(
                    window_id=window.id,
                    operation_id=window.operation_id,
                    directory=str(window.directory),
                    partial=str(partial),
                    lock_path=lock_path,
                    live_cleanup_job_id=completed[-1],
                    bytes=len(_PARTIAL),
                    sha256=hashlib.sha256(_PARTIAL).hexdigest(),
                )
                threading.Event().wait()


def converge(identifier: str, operation_id: str, prior_cleanup_job_id: str) -> None:
    from app.main import app

    ensure_dirs()
    with TestClient(app):
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            with get_session_factory().scoped_session() as session:
                receipt = session.get(IngestionScratchWindow, identifier)
                reservation = session.get(CapacityReservation, operation_id)
                retired = receipt is None and reservation is None
            completed = _completed(identifier)
            if retired and any(job_id != prior_cleanup_job_id for job_id in completed):
                _emit(
                    receipt_present=False,
                    reservation_present=False,
                    completed_cleanup_job_ids=completed,
                )
                return
            assert time.monotonic() < deadline, _diagnostics()
            time.sleep(0.1)


if __name__ == "__main__":
    faulthandler.dump_traceback_later(85, exit=True)
    role = sys.argv[1]
    if role == "stall":
        stall()
    elif role == "converge":
        converge(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(f"unknown scratch role {role}")
