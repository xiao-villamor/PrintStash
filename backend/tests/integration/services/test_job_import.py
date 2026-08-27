"""Moonraker history import is model-scoped, idempotent, and state-faithful.

The service persists only matching history and must never duplicate a printer's
reported filename when its casing changes between polls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Model,
    Printer,
    PrintJob,
    PrintJobState,
)
from app.services import job_import, runtime_config
from app.services.moonraker import MoonrakerError

_seed_counter = 0


def _seed_model_and_file(db_session: Session, filename: str = "Benchy.gcode") -> File:
    global _seed_counter
    _seed_counter += 1
    m = Model(name="Model", slug=f"model-{_seed_counter}", hash=f"{_seed_counter:064x}")
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    f = File(
        model_id=m.id,
        path="/data/benchy.gcode",
        original_filename=filename,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=100,
        sha256="c" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


def _seed_printer(db_session: Session) -> Printer:
    p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _import_history(
    session: Session, *, model_id: int, printer_id: int, history: list[dict]
):
    return job_import._import_history_with_session(
        session,
        history,
        model_id=model_id,
        printer_id=printer_id,
    )


def test_import_matching_history_persists_printer_history_job(
    db_session: Session,
) -> None:
    file_row = _seed_model_and_file(db_session)
    printer = _seed_printer(db_session)

    results = _import_history(
        db_session,
        model_id=file_row.model_id,
        printer_id=printer.id,
        history=[{"filename": "Benchy.gcode", "status": "completed"}],
    )

    job = db_session.exec(
        select(PrintJob).where(PrintJob.model_id == file_row.model_id)
    ).one()
    assert results[0].imported is True
    assert job.source == "printer_history"
    assert job.file_id == file_row.id


def test_import_dedups_case_insensitively(db_session: Session):
    f = _seed_model_and_file(db_session, filename="Benchy.gcode")
    p = _seed_printer(db_session)
    db_session.add(
        PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=f.model_id,
            remote_filename="Benchy.gcode",
            state=PrintJobState.COMPLETED,
        )
    )
    db_session.commit()

    results = _import_history(
        db_session,
        model_id=f.model_id,
        printer_id=p.id,
        history=[{"filename": "benchy.gcode", "status": "completed"}],
    )

    assert results[0].imported is False
    jobs = db_session.exec(
        select(PrintJob).where(PrintJob.model_id == f.model_id)
    ).all()
    assert len(jobs) == 1


def test_import_is_idempotent(db_session: Session):
    f = _seed_model_and_file(db_session)
    p = _seed_printer(db_session)
    history = [{"filename": "Benchy.gcode", "status": "completed"}]

    _import_history(db_session, model_id=f.model_id, printer_id=p.id, history=history)
    _import_history(db_session, model_id=f.model_id, printer_id=p.id, history=history)

    jobs = db_session.exec(
        select(PrintJob).where(PrintJob.model_id == f.model_id)
    ).all()
    assert len(jobs) == 1


@pytest.mark.parametrize(
    ("remote_status", "expected_state"),
    [
        pytest.param("completed", PrintJobState.COMPLETED, id="completed"),
        pytest.param("cancelled", PrintJobState.CANCELLED, id="cancelled"),
        pytest.param("error", PrintJobState.FAILED, id="error"),
    ],
)
def test_import_maps_terminal_provider_states(
    db_session: Session,
    remote_status: str,
    expected_state: PrintJobState,
) -> None:
    f = _seed_model_and_file(db_session, filename=f"{remote_status}.gcode")
    p = _seed_printer(db_session)
    history = [{"filename": f"{remote_status}.gcode", "status": remote_status}]

    _import_history(db_session, model_id=f.model_id, printer_id=p.id, history=history)

    job = db_session.exec(select(PrintJob).where(PrintJob.model_id == f.model_id)).one()
    assert job.state == expected_state


def test_import_skips_files_not_in_this_model(db_session: Session):
    f = _seed_model_and_file(db_session, filename="Benchy.gcode")
    p = _seed_printer(db_session)

    results = _import_history(
        db_session,
        model_id=f.model_id,
        printer_id=p.id,
        history=[{"filename": "unrelated.gcode", "status": "completed"}],
    )

    assert results == []
    jobs = db_session.exec(
        select(PrintJob).where(PrintJob.model_id == f.model_id)
    ).all()
    assert jobs == []


def test_import_marks_a_successful_revision_known_good_when_configured(
    db_session: Session,
) -> None:
    f = _seed_model_and_file(db_session)
    p = _seed_printer(db_session)

    _import_history(
        db_session,
        model_id=f.model_id,
        printer_id=p.id,
        history=[{"filename": "Benchy.gcode", "status": "completed"}],
    )

    db_session.refresh(f)
    assert f.revision_status == FileRevisionStatus.KNOWN_GOOD


def test_import_preserves_revision_status_when_auto_promotion_is_disabled(
    db_session: Session,
) -> None:
    f = _seed_model_and_file(db_session)
    p = _seed_printer(db_session)
    runtime_config.set_auto_mark_known_good(db_session, False)

    _import_history(
        db_session,
        model_id=f.model_id,
        printer_id=p.id,
        history=[{"filename": "Benchy.gcode", "status": "completed"}],
    )

    db_session.refresh(f)
    assert f.revision_status is None


def test_import_reports_provider_history_failure_without_partial_rows(
    db_session: Session,
) -> None:
    f = _seed_model_and_file(db_session)
    p = _seed_printer(db_session)

    with (
        patch(
            "app.services.job_import.MoonrakerClient.get_print_history",
            new=AsyncMock(side_effect=MoonrakerError("history unavailable")),
        ),
        pytest.raises(MoonrakerError, match="history unavailable"),
    ):
        asyncio.run(
            job_import.import_print_jobs_from_printer(
                model_id=f.model_id,
                printer_id=p.id,
                moonraker_url="http://10.0.0.1:7125",
                moonraker_api_key=None,
            )
        )

    jobs = db_session.exec(
        select(PrintJob).where(PrintJob.model_id == f.model_id)
    ).all()
    assert jobs == []
