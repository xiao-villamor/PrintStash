"""Tests for services.job_import.import_print_jobs_from_printer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from sqlmodel import Session, select

from app.db.models import File, FileType, Printer, PrintJob, PrintJobState
from app.services import job_import
from tests.factories import build_file, build_model, build_print_job, build_printer

_seed_counter = 0


def _seed_model_and_file(db_session: Session, filename: str = "Benchy.gcode") -> File:
    global _seed_counter
    _seed_counter += 1
    m = build_model(
        db_session,
        name="Model",
        slug=f"model-{_seed_counter}",
        hash=f"{_seed_counter:064x}",
    )

    f = build_file(
        db_session,
        m,
        path="/data/benchy.gcode",
        filename=filename,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=100,
        sha256="c" * 64,
    )
    return f


def _seed_printer(db_session: Session) -> Printer:
    p = build_printer(db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125")
    return p


def _run_import(
    session: Session, *, model_id: int, printer_id: int, history: list[dict]
):
    with patch(
        "app.services.job_import.MoonrakerClient.get_print_history",
        new=AsyncMock(return_value=history),
    ):
        return asyncio.run(
            job_import.import_print_jobs_from_printer(
                model_id=model_id,
                printer_id=printer_id,
                moonraker_url="http://10.0.0.1:7125",
                moonraker_api_key=None,
            )
        )


class TestImportPrinterJobs:
    def test_import_dedups_case_insensitively(self, db_session: Session):
        f = _seed_model_and_file(db_session, filename="Benchy.gcode")
        p = _seed_printer(db_session)
        build_print_job(
            db_session,
            f,
            printer=p,
            remote_filename="Benchy.gcode",
            state=PrintJobState.COMPLETED,
        )

        results = _run_import(
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

    def test_import_is_idempotent(self, db_session: Session):
        f = _seed_model_and_file(db_session)
        p = _seed_printer(db_session)
        history = [{"filename": "Benchy.gcode", "status": "completed"}]

        _run_import(db_session, model_id=f.model_id, printer_id=p.id, history=history)
        _run_import(db_session, model_id=f.model_id, printer_id=p.id, history=history)

        jobs = db_session.exec(
            select(PrintJob).where(PrintJob.model_id == f.model_id)
        ).all()
        assert len(jobs) == 1

    def test_import_maps_moonraker_status_to_job_state(self, db_session: Session):
        f = _seed_model_and_file(db_session)
        p = _seed_printer(db_session)
        history = [
            {"filename": "Benchy.gcode", "status": "completed"},
        ]
        _run_import(db_session, model_id=f.model_id, printer_id=p.id, history=history)
        job = db_session.exec(
            select(PrintJob).where(PrintJob.model_id == f.model_id)
        ).one()
        assert job.state == PrintJobState.COMPLETED

        f2 = _seed_model_and_file(db_session, filename="Other.gcode")
        history2 = [{"filename": "Other.gcode", "status": "cancelled"}]
        _run_import(db_session, model_id=f2.model_id, printer_id=p.id, history=history2)
        job2 = db_session.exec(
            select(PrintJob).where(PrintJob.model_id == f2.model_id)
        ).one()
        assert job2.state == PrintJobState.CANCELLED

    def test_import_skips_files_not_in_this_model(self, db_session: Session):
        f = _seed_model_and_file(db_session, filename="Benchy.gcode")
        p = _seed_printer(db_session)

        results = _run_import(
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
