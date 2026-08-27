"""Printer-history import routes persist matching jobs once through real services."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import select

from app.db.models import File, FileType, Model, Printer, PrintJob, User
from app.db.session import get_session_factory
from app.services.auth import create_access_token, hash_password


def _seed() -> tuple[int, int, dict[str, str]]:
    with get_session_factory().scoped_session() as session:
        user = User(
            username="history-admin",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        model = Model(name="Benchy", slug="benchy", hash="h" * 64)
        session.add(model)
        session.flush()
        session.add(
            File(
                model_id=model.id,
                path="benchy.gcode",
                original_filename="Benchy.gcode",
                file_type=FileType.GCODE,
                size_bytes=100,
                sha256="g" * 64,
            )
        )
        printer = Printer(name="Klipper", moonraker_url="http://10.0.0.1:7125")
        session.add(printer)
        session.commit()
        session.refresh(user)
        session.refresh(model)
        session.refresh(printer)
        token = create_access_token(user.id, user.username, scope="admin")
        return model.id, printer.id, {"Authorization": f"Bearer {token}"}


def _history() -> list[dict]:
    return [
        {
            "filename": "Benchy.gcode",
            "status": "completed",
            "print_duration": 120,
            "start_time": 1_767_225_600,
            "end_time": 1_767_225_720,
        }
    ]


class TestImportPrintJobsFromPrinter:
    def test_imports_matching_print_history_from_a_supported_printer(
        self, client: TestClient, file_backed_integration_db: None
    ) -> None:
        model_id, printer_id, headers = _seed()

        with patch(
            "app.services.job_import.MoonrakerClient.get_print_history",
            new=AsyncMock(return_value=_history()),
        ):
            response = client.post(
                f"/api/v1/models/{model_id}/print-jobs/import-printer/{printer_id}",
                headers=headers,
            )

        with get_session_factory().scoped_session() as session:
            jobs = session.exec(
                select(PrintJob).where(PrintJob.model_id == model_id)
            ).all()
        assert response.status_code == 200, response.text
        assert response.json()[0]["imported"] is True
        assert len(jobs) == 1

    def test_deduplicates_replayed_printer_history(
        self, client: TestClient, file_backed_integration_db: None
    ) -> None:
        model_id, printer_id, headers = _seed()

        with patch(
            "app.services.job_import.MoonrakerClient.get_print_history",
            new=AsyncMock(return_value=_history()),
        ):
            first = client.post(
                f"/api/v1/models/{model_id}/print-jobs/import-printer/{printer_id}",
                headers=headers,
            )
            second = client.post(
                f"/api/v1/models/{model_id}/print-jobs/import-printer/{printer_id}",
                headers=headers,
            )

        with get_session_factory().scoped_session() as session:
            jobs = session.exec(
                select(PrintJob).where(PrintJob.model_id == model_id)
            ).all()
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()[0]["imported"] is False
        assert len(jobs) == 1
