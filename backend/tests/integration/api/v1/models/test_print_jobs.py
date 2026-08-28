"""A model's print history: what was printed, where, and how it went.

Print history is what turns a library into a record of what actually works, so the two
things it must get right are attribution and arithmetic. A job can name a printer
PrintStash knows (and is then linked to it, so retiring that printer keeps the history) or
a printer it does not — someone's garage machine typed in by hand — and both are valid;
what is not valid is a job with no printer at all, because an unattributed print says
nothing.

The per-artifact outcome summary is the arithmetic: a success rate over the jobs that
finished, an average duration, and a **zero** for a mesh that was never printed rather
than an absent row. Asking about a file that is not there is a 404, not a silent gap in
the comparison.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    FileType,
    Metadata,
    Model,
    Printer,
    PrinterFile,
    PrintJobState,
)
from tests.factories import (
    build_print_job,
    build_printer,
)


@pytest.fixture
def model(make_model) -> Model:
    return make_model("Bracket")


@pytest.fixture
def printer(db_session: Session) -> Printer:
    row = build_printer(db_session, name="Ender", moonraker_url="http://10.0.0.1:7125")
    return row


class TestModelPrinterFiles:
    def test_lists_the_printers_holding_a_revision(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
        printer: Printer,
    ) -> None:
        row = make_file(model)
        db_session.add(
            PrinterFile(
                printer_id=printer.id,
                file_id=row.id,
                remote_filename="file-1.gcode",
                matched_by="upload_history",
            )
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/models/{model.id}/printer-files", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        listed = response.json()[0]
        assert listed["file_id"] == row.id
        assert listed["printer_name"] == "Ender"
        assert listed["matched_by"] == "upload_history"

    def test_says_a_revision_is_on_no_printer(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        make_file(model)

        response = client.get(
            f"/api/v1/models/{model.id}/printer-files", headers=auth_headers
        )

        assert response.json() == []

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model
    ) -> None:
        assert client.get(f"/api/v1/models/{model.id}/printer-files").status_code == 401


class TestListModelPrintJobs:
    def test_says_a_model_has_never_been_printed(
        self, client: TestClient, auth_headers, model: Model
    ) -> None:
        response = client.get(
            f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_names_the_printer_each_job_ran_on(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
        printer: Printer,
    ) -> None:
        row = make_file(model)
        build_print_job(
            db_session,
            row,
            printer=printer,
            remote_filename="file-1.gcode",
            state=PrintJobState.COMPLETED,
        )

        response = client.get(
            f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
        )

        assert response.json()[0]["printer_name"] == "Ender"
        assert response.json()[0]["state"] == "completed"

    def test_carries_the_revision_the_job_printed(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
        printer: Printer,
    ) -> None:
        row = make_file(model)
        row.revision_label = "PETG baseline"
        db_session.add(row)
        build_print_job(
            db_session,
            row,
            printer=printer,
            remote_filename="file-1.gcode",
            state=PrintJobState.COMPLETED,
        )

        listed = client.get(
            f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
        ).json()[0]

        # "Which revision was this?" is the first question about a failed print.
        assert listed["revision_label"] == "PETG baseline"
        assert listed["gcode_revision_number"] == 1

    def test_carries_the_material_the_job_was_sliced_for(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
        printer: Printer,
    ) -> None:
        row = make_file(model)
        db_session.add(
            Metadata(file_id=row.id, slicer_name="OrcaSlicer", material_type="PETG")
        )
        build_print_job(
            db_session,
            row,
            printer=printer,
            remote_filename="file-1.gcode",
            state=PrintJobState.COMPLETED,
        )

        listed = client.get(
            f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
        ).json()[0]

        assert listed["material_type"] == "PETG"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model
    ) -> None:
        assert client.get(f"/api/v1/models/{model.id}/print-jobs").status_code == 401


class TestCreateManualPrintJob:
    def test_links_a_job_to_a_printer_the_library_knows(
        self,
        client: TestClient,
        auth_headers,
        model: Model,
        make_file,
        printer: Printer,
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"file_id": row.id, "printer_id": printer.id},
        )

        assert response.status_code == 200, response.text
        assert response.json()["printer_id"] == printer.id
        assert response.json()["printer_name"] == "Ender"

    def test_accepts_a_printer_the_library_does_not_know(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={
                "printer_name": "Garage Prusa",
                "file_id": row.id,
                "state": "completed",
            },
        )

        # Someone's garage machine is still a printer worth recording.
        assert response.status_code == 200, response.text
        assert response.json()["printer_id"] is None
        assert response.json()["printer_name"] == "Garage Prusa"

    def test_shows_the_new_job_in_the_history(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        row = make_file(model)
        client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"printer_name": "Garage Prusa", "file_id": row.id},
        )

        listed = client.get(
            f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
        ).json()

        assert listed[0]["printer_name"] == "Garage Prusa"

    def test_refuses_a_job_with_no_printer_at_all(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"file_id": row.id, "printer_name": "   "},
        )

        # An unattributed print says nothing about what works.
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "printer_required"

    def test_refuses_a_state_it_does_not_recognise(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={
                "file_id": row.id,
                "printer_name": "Bench",
                "state": "not_a_real_state",
            },
        )

        assert response.status_code == 422, response.text

    def test_refuses_a_file_that_does_not_exist(
        self, client: TestClient, auth_headers, model: Model
    ) -> None:
        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"file_id": 999999, "printer_name": "Bench"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "file_not_found"

    def test_refuses_a_file_that_is_in_the_trash(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
    ) -> None:
        row = make_file(model)
        row.deleted_at = utcnow()
        db_session.add(row)
        db_session.commit()

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"file_id": row.id, "printer_name": "Bench"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "file_not_found"

    def test_refuses_a_printer_id_that_does_not_exist(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            headers=auth_headers,
            json={"file_id": row.id, "printer_id": 999999},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "printer_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs",
            json={"file_id": row.id, "printer_name": "Bench"},
        )

        assert response.status_code == 401, response.text


class TestImportPrintJobsFromPrinter:
    def test_refuses_a_printer_with_no_address_to_ask(
        self, client: TestClient, db_session: Session, auth_headers, model: Model
    ) -> None:
        # Explicitly addressless: `build_printer` fills in the provider's
        # connection details, so the omission has to be stated or the endpoint
        # gets a reachable printer and this row asserts nothing.
        printer = build_printer(db_session, name="No URL", moonraker_url="")

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs/import-printer/{printer.id}",
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "printer_no_url"

    def test_refuses_a_printer_that_does_not_exist(
        self, client: TestClient, auth_headers, model: Model
    ) -> None:
        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs/import-printer/999999",
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "printer_not_found"

    def test_reports_a_printer_it_cannot_reach(
        self,
        client: TestClient,
        auth_headers,
        model: Model,
        printer: Printer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.moonraker import MoonrakerError

        async def unreachable(*_args: object, **_kwargs: object):
            raise MoonrakerError("unreachable")

        monkeypatch.setattr(
            "app.api.v1.models.job_import.import_print_jobs_from_printer", unreachable
        )

        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs/import-printer/{printer.id}",
            headers=auth_headers,
        )

        # 502, not 500: the fault is the printer's, and a retry may work.
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "printer_unreachable"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model, printer: Printer
    ) -> None:
        response = client.post(
            f"/api/v1/models/{model.id}/print-jobs/import-printer/{printer.id}"
        )

        assert response.status_code == 401, response.text


class TestArtifactOutcomes:
    def test_summarizes_how_a_revision_has_printed(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        model: Model,
        make_file,
    ) -> None:
        gcode = make_file(model)
        build_print_job(
            db_session,
            gcode,
            state=PrintJobState.COMPLETED,
            source="manual",
            actual_duration_s=100,
            filament_g_effective=12.5,
            cost=1.25,
        )
        build_print_job(
            db_session,
            gcode,
            state=PrintJobState.FAILED,
            source="manual",
            actual_duration_s=50,
        )

        response = client.get(
            f"/api/v1/models/{model.id}/artifact-outcomes?file_id={gcode.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        row = response.json()[0]
        assert row["completed_count"] == 1
        assert row["failed_count"] == 1
        assert row["success_rate"] == 0.5
        assert row["average_duration_s"] == 75

    def test_reports_zero_for_a_file_that_was_never_printed(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        mesh = make_file(model, file_type=FileType.STL)

        response = client.get(
            f"/api/v1/models/{model.id}/artifact-outcomes?file_id={mesh.id}",
            headers=auth_headers,
        )

        # A zero row and an absent row read very differently in a comparison.
        assert response.json()[0]["print_count"] == 0

    def test_compares_several_files_in_one_answer(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        mesh = make_file(model, file_type=FileType.STL)
        gcode = make_file(model)

        response = client.get(
            f"/api/v1/models/{model.id}/artifact-outcomes"
            f"?file_id={mesh.id}&file_id={gcode.id}",
            headers=auth_headers,
        )

        assert {row["file_id"] for row in response.json()} == {mesh.id, gcode.id}

    def test_refuses_when_any_named_file_does_not_exist(
        self, client: TestClient, auth_headers, model: Model, make_file
    ) -> None:
        real = make_file(model, file_type=FileType.STL)

        response = client.get(
            f"/api/v1/models/{model.id}/artifact-outcomes"
            f"?file_id={real.id}&file_id=999999",
            headers=auth_headers,
        )

        # A silent gap would read as "that revision has never been printed".
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, model: Model, make_file
    ) -> None:
        row = make_file(model)

        response = client.get(
            f"/api/v1/models/{model.id}/artifact-outcomes?file_id={row.id}"
        )

        assert response.status_code == 401, response.text
