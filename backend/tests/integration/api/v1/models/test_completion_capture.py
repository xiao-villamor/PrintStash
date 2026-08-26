"""Defends completion capture at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    File,
    FileRevisionStatus,
    Printer,
    PrintJob,
    PrintJobState,
    _make_model,
    asyncio,
    pytest,
)


class TestCompletionCapture:
    def _setup(self, db_session, *, revision_status=None):
        m = _make_model(db_session, slug="cap", hash_="c" * 64)
        f = File(
            model_id=m.id,
            path="/data/cap.gcode",
            original_filename="cap.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="g" * 64,
            revision_status=revision_status,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        p = Printer(name="Cap", moonraker_url="http://10.0.0.9:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        job = PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=m.id,
            remote_filename="cap.gcode",
            state=PrintJobState.PRINTING,
            source="vault",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return p.id, f.id, job.id

    def test_completion_captures_filament_and_duration(self, hub, db_session):
        pid, file_id, job_id = self._setup(db_session)

        asyncio.run(
            hub._sync_active_job(
                pid,
                "complete",
                "cap.gcode",
                1.0,
                {
                    "state": "complete",
                    "filename": "cap.gcode",
                    "filament_used": 2000.0,
                    "total_duration": 3600,
                },
            )
        )
        job = db_session.get(PrintJob, job_id)
        db_session.refresh(job)
        assert job.state == PrintJobState.COMPLETED
        assert job.filament_used_mm == pytest.approx(2000.0)
        assert job.filament_used_g is not None and job.filament_used_g > 0
        assert job.actual_duration_s == 3600

    def test_completion_auto_marks_known_good(self, hub, db_session):
        pid, file_id, job_id = self._setup(db_session)
        asyncio.run(
            hub._sync_active_job(
                pid,
                "complete",
                "cap.gcode",
                1.0,
                {"state": "complete", "filename": "cap.gcode"},
            )
        )
        f = db_session.get(File, file_id)
        db_session.refresh(f)
        assert f.revision_status == FileRevisionStatus.KNOWN_GOOD

    def test_completion_does_not_override_manual_failed(self, hub, db_session):
        pid, file_id, job_id = self._setup(
            db_session, revision_status=FileRevisionStatus.FAILED
        )
        asyncio.run(
            hub._sync_active_job(
                pid,
                "complete",
                "cap.gcode",
                1.0,
                {"state": "complete", "filename": "cap.gcode"},
            )
        )
        f = db_session.get(File, file_id)
        db_session.refresh(f)
        assert f.revision_status == FileRevisionStatus.FAILED
