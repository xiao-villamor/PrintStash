"""Connected printer jobs snapshot rates and settle using terminal evidence."""

import pytest
from sqlmodel import Session

from app.db.models import PrintJobState
from app.modules.printing.printer_jobs import _claim_next_sync
from app.modules.printing.production_costing import launch_job_cost
from tests.factories import (
    build_file,
    build_model,
    build_print_job,
    build_printer,
    build_printer_profile,
)

_EXPENSES = {
    "acquisition_cost": 500,
    "lifetime_hours": 1000,
    "lifetime_maintenance": 100,
    "power_watts": 200,
    "electricity_per_kwh": 0.25,
    "extra_per_hour": 1,
}


class TestConnectedPrinterCosting:
    def test_claim_uses_the_assigned_printers_rates(self, db_session: Session) -> None:
        profile = build_printer_profile(db_session, expenses=_EXPENSES)
        printer = build_printer(db_session, cost_profile_id=profile.id)
        artifact = build_file(
            db_session,
            build_model(db_session),
            filename="hook.gcode",
            metadata={"estimated_time_s": 3600},
        )
        job = build_print_job(db_session, artifact, printer=printer)

        assert _claim_next_sync() == job.id
        db_session.refresh(job)
        assert job.state is PrintJobState.UPLOADING
        assert job.production_cost == {
            "currency": "USD",
            "material": None,
            "machine": 1.65,
            "total": 1.65,
            "estimated": True,
        }

    @pytest.mark.parametrize(
        ("provider_state", "state"),
        [
            ("complete", PrintJobState.COMPLETED),
            ("error", PrintJobState.FAILED),
            ("cancelled", PrintJobState.CANCELLED),
        ],
    )
    def test_settles_a_managed_print_at_saved_rates(
        self, db_session: Session, hub, provider_state: str, state: PrintJobState
    ) -> None:
        profile = build_printer_profile(db_session, expenses=_EXPENSES)
        printer = build_printer(db_session, cost_profile_id=profile.id)
        artifact = build_file(
            db_session,
            build_model(db_session),
            filename="hook.gcode",
            metadata={
                "material_type": "PLA",
                "filament_weight_g": 100,
                "filament_cost": 2,
                "estimated_time_s": 3600,
            },
        )
        job = build_print_job(
            db_session, artifact, printer=printer, state=PrintJobState.STARTED
        )
        launch_job_cost(db_session, printer, job, artifact)
        assert job.production_cost == {
            "currency": "USD",
            "material": 2,
            "machine": 1.65,
            "total": 3.65,
            "estimated": True,
        }

        profile.expenses = {**_EXPENSES, "extra_per_hour": 50}
        db_session.add(profile)
        db_session.add(job)
        db_session.commit()
        progress = 1 if state == PrintJobState.COMPLETED else 0.4
        hub._sync_active_job_db(
            printer.id,
            provider_state,
            job.remote_filename,
            progress,
            {"total_duration": 1800, "filament_used": 1000},
        )
        db_session.refresh(job)
        assert job.state == state
        assert job.production_cost == {
            "currency": "USD",
            "material": 0.0596,
            "machine": 0.825,
            "total": 0.8846,
            "estimated": False,
        }

    def test_keeps_last_usage_when_stop_resets_telemetry(
        self, db_session: Session, hub
    ) -> None:
        profile = build_printer_profile(db_session, expenses=_EXPENSES)
        printer = build_printer(db_session, cost_profile_id=profile.id)
        artifact = build_file(
            db_session,
            build_model(db_session),
            filename="hook.gcode",
            metadata={
                "material_type": "PLA",
                "filament_weight_g": 100,
                "filament_cost": 2,
            },
        )
        job = build_print_job(
            db_session, artifact, printer=printer, state=PrintJobState.STARTED
        )
        launch_job_cost(db_session, printer, job, artifact)
        db_session.add(job)
        db_session.commit()
        hub._sync_active_job_db(
            printer.id,
            "printing",
            job.remote_filename,
            0.4,
            {"total_duration": 1800, "filament_used": 1000},
        )
        hub._sync_active_job_db(
            printer.id,
            "cancelled",
            job.remote_filename,
            0,
            {"total_duration": 0, "filament_used": 0},
        )
        db_session.refresh(job)
        assert job.state == PrintJobState.CANCELLED
        assert job.production_cost["total"] == 0.8846
        assert job.progress == pytest.approx(0.4)
