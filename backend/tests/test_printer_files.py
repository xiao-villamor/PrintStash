from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import File, Model, PrintJob, Printer, PrinterFile
from app.services.printer_files import sync_printer_files


def _make_gcode(
    session: Session,
    *,
    name: str = "part.gcode",
    size: int = 123,
    model_slug: str = "part",
) -> tuple[Model, File]:
    model = Model(name=model_slug.title(), slug=model_slug, hash=model_slug[0] * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    f = File(
        model_id=model.id,
        path=f"/data/{name}",
        original_filename=name,
        file_type="gcode",
        version=1,
        size_bytes=size,
        sha256="f" * 64,
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return model, f


def test_sync_matches_upload_history_first(db_session: Session):
    _, f = _make_gcode(db_session)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    job = PrintJob(
        printer_id=printer.id,
        file_id=f.id,
        model_id=f.model_id,
        remote_filename="folder/custom-name.gcode",
    )
    db_session.add(job)
    db_session.commit()

    rows = sync_printer_files(
        db_session,
        printer_id=printer.id,
        remote_files=[{"path": "folder/custom-name.gcode", "size": 999}],
    )

    assert len(rows) == 1
    assert rows[0].file_id == f.id
    assert rows[0].matched_by == "upload_history"


def test_sync_does_not_match_external_job_as_upload_history(db_session: Session):
    _, f = _make_gcode(db_session)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    job = PrintJob(
        printer_id=printer.id,
        file_id=f.id,
        model_id=f.model_id,
        remote_filename="external-name.gcode",
        source="external",
    )
    db_session.add(job)
    db_session.commit()

    rows = sync_printer_files(
        db_session,
        printer_id=printer.id,
        remote_files=[{"path": "external-name.gcode", "size": 999}],
    )

    assert rows[0].file_id is None
    assert rows[0].matched_by == "external"


def test_sync_matches_filename_then_marks_missing(db_session: Session):
    _, f = _make_gcode(db_session, name="bracket.gcode", size=456)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    rows = sync_printer_files(
        db_session,
        printer_id=printer.id,
        remote_files=[{"path": "subdir/bracket.gcode", "size": 456}],
    )
    assert rows[0].file_id == f.id
    assert rows[0].matched_by == "filename"
    assert rows[0].missing_since is None

    rows = sync_printer_files(db_session, printer_id=printer.id, remote_files=[])
    assert rows[0].missing_since is not None


def test_sync_keeps_unmatched_external_file(db_session: Session):
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    sync_printer_files(
        db_session,
        printer_id=printer.id,
        remote_files=[{"path": "external.gcode", "size": 789}],
    )

    row = db_session.exec(select(PrinterFile)).one()
    assert row.file_id is None
    assert row.matched_by == "external"
