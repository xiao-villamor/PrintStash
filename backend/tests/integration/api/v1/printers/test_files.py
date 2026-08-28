"""What is on the printer's own storage, and starting a print from it.

The printer holds files PrintStash did not put there, and PrintStash holds files the
printer has never seen. This surface reconciles the two: it lists what is really on the
machine, matches those names back to library revisions where it can, and starts one.

Starting a print is the sharpest edge here, because the failure modes are all "the printer
disagrees": the file was deleted between listing and starting, the machine is already
printing, or the operator has not released it. Each answers with its own code, because the
operator's next action is different in each case.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CollectionRole,
    File,
    FileType,
    PrinterFile,
    PrintJob,
    PrintJobState,
    User,
)
from app.services.printer_provider import ProviderError
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_printer,
    grant_collection_role,
)
from tests.integration.api.v1.printers._helpers import grant_printer, user_headers


class TestPrinterFiles:
    def _setup_file(self, db_session: Session):

        m = build_model(db_session, name="Bracket", slug="bracket", hash="b" * 64)
        f = build_file(
            db_session,
            m,
            path="/data/bracket.gcode",
            filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="c" * 64,
        )
        return m, f

    def test_list_printer_files_reports_files_we_do_not_own(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import PrinterFile

        m, f = self._setup_file(db_session)
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                file_id=f.id,
                remote_filename="bracket.gcode",
                size_bytes=100,
                matched_by="filename",
            )
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="external.gcode",
                size_bytes=200,
                matched_by="external",
            )
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{p.id}/files", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        matched = next(row for row in data if row["remote_filename"] == "bracket.gcode")
        assert matched["file_id"] == f.id
        assert matched["model_id"] == m.id
        assert matched["model_name"] == "Bracket"
        external = next(
            row for row in data if row["remote_filename"] == "external.gcode"
        )
        assert external["file_id"] is None

    def test_sync_printer_files_requires_auth(
        self, client: TestClient, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        resp = client.post(f"/api/v1/printers/{p.id}/files/sync")
        assert resp.status_code == 401

    def test_sync_printer_files_uses_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._setup_file(db_session)
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [{"path": "bracket.gcode", "size": 100}]
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["file_id"] == f.id
        assert data[0]["matched_by"] == "filename"

    def test_sync_printer_files_deletes_remote_files_missing_from_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="deleted-in-mainsail.gcode",
                matched_by="external",
            )
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="still-there.gcode",
                matched_by="external",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [{"path": "still-there.gcode", "size": 123}]
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["still-there.gcode"]
        remaining = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).all()
        assert [row.remote_filename for row in remaining] == ["still-there.gcode"]

    def test_deleting_a_printer_file_resyncs_the_inventory(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        deleted = PrinterFile(
            printer_id=p.id,
            remote_filename="deleted.gcode",
            matched_by="external",
        )
        kept = PrinterFile(
            printer_id=p.id,
            remote_filename="kept.gcode",
            matched_by="external",
        )
        db_session.add_all([deleted, kept])
        db_session.commit()
        db_session.refresh(deleted)

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.delete_file",
                new_callable=AsyncMock,
            ) as mock_delete,
            patch(
                "app.services.printer_provider.MoonrakerProvider.list_files",
                new_callable=AsyncMock,
            ) as mock_list,
        ):
            mock_delete.return_value = {"result": "ok"}
            mock_list.return_value = [{"path": "kept.gcode", "size": 123}]
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{deleted.id}",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["kept.gcode"]
        mock_delete.assert_awaited_once_with("deleted.gcode")
        remaining = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).all()
        assert [row.remote_filename for row in remaining] == ["kept.gcode"]

    def test_sync_unsupported_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        resp = client.post(f"/api/v1/printers/{p.id}/files/sync", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_non_superuser_cannot_list_printer_files(
        self, client: TestClient, db_session: Session
    ):
        headers = user_headers(db_session, "viewer")
        viewer = db_session.exec(select(User).where(User.username == "viewer")).one()
        allowed = build_collection(
            db_session, name="Allowed", slug="allowed", path="allowed"
        )
        denied = build_collection(
            db_session, name="Denied", slug="denied", path="denied"
        )
        db_session.add_all([allowed, denied])
        db_session.commit()
        db_session.refresh(allowed)
        db_session.refresh(denied)
        grant_collection_role(db_session, viewer, allowed, CollectionRole.VIEW)
        allowed_model = build_model(
            db_session,
            name="Allowed model",
            slug="allowed-model",
            hash="1" * 64,
            collection_id=allowed.id,
        )
        denied_model = build_model(
            db_session,
            name="Denied model",
            slug="denied-model",
            hash="2" * 64,
            collection_id=denied.id,
        )
        db_session.add_all([allowed_model, denied_model])
        db_session.commit()
        db_session.refresh(allowed_model)
        db_session.refresh(denied_model)
        allowed_file = build_file(
            db_session,
            allowed_model,
            path="/data/allowed.gcode",
            filename="allowed.gcode",
            file_type=FileType.GCODE,
            size_bytes=100,
            sha256="3" * 64,
        )
        denied_file = build_file(
            db_session,
            denied_model,
            path="/data/denied.gcode",
            filename="denied.gcode",
            file_type=FileType.GCODE,
            size_bytes=100,
            sha256="4" * 64,
        )
        printer = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add_all([allowed_file, denied_file, printer])
        db_session.commit()
        db_session.refresh(allowed_file)
        db_session.refresh(denied_file)
        db_session.refresh(printer)
        db_session.add_all(
            [
                PrinterFile(
                    printer_id=printer.id,
                    file_id=allowed_file.id,
                    remote_filename="allowed.gcode",
                    matched_by="filename",
                ),
                PrinterFile(
                    printer_id=printer.id,
                    file_id=denied_file.id,
                    remote_filename="denied.gcode",
                    matched_by="filename",
                ),
                PrinterFile(
                    printer_id=printer.id,
                    remote_filename="external.gcode",
                    matched_by="external",
                ),
            ]
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{printer.id}/files", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_non_superuser_cannot_send_to_printer(
        self, client: TestClient, db_session: Session
    ):
        headers = user_headers(db_session, "editor")
        collection = build_collection(
            db_session, name="Private", slug="private", path="private"
        )
        model = build_model(
            db_session,
            name="Private model",
            slug="private-model",
            hash="5" * 64,
            collection_id=collection.id,
        )
        file_row = build_file(
            db_session,
            model,
            path="/data/private.gcode",
            filename="private.gcode",
            file_type=FileType.GCODE,
            size_bytes=100,
            sha256="6" * 64,
        )
        printer = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add_all([file_row, printer])
        db_session.commit()
        db_session.refresh(file_row)
        db_session.refresh(printer)

        resp = client.post(
            f"/api/v1/printers/{printer.id}/send",
            json={"file_id": file_row.id, "start_print": False},
            headers=headers,
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_start_matched_printer_file_creates_vault_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import PrinterFile

        m, f = self._setup_file(db_session)
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                file_id=f.id,
                remote_filename="folder/bracket.gcode",
                matched_by="upload_history",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
        ) as mock_start:
            mock_start.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "folder/bracket.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["file_id"] == f.id
        assert data["model_id"] == m.id
        assert data["source"] == "vault"
        assert data["state"] == "started"
        mock_start.assert_awaited_once_with("folder/bracket.gcode")

    def test_start_external_printer_file_creates_external_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import SENTINEL_FILE_HASH, PrinterFile

        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="external.gcode",
                matched_by="external",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
        ) as mock_start:
            mock_start.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "external.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "external"
        sentinel_file = db_session.get(File, data["file_id"])
        assert sentinel_file is not None
        assert sentinel_file.sha256 == SENTINEL_FILE_HASH
        mock_start.assert_awaited_once_with("external.gcode")

    def test_start_bambu_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )

        with patch(
            "app.services.printer_provider.BambuLanProvider.start",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "part.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200

    def test_sync_provider_error_sets_last_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        db_session.refresh(p)
        assert p.last_error == "boom"

    def test_delete_file_unsupported_provider_409(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )

        resp = client.delete(f"/api/v1/printers/{p.id}/files/1", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_delete_file_404_unknown_printer_file(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        resp = client.delete(
            f"/api/v1/printers/{p.id}/files/99999", headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_file_not_found"

    def test_delete_file_provider_error_sets_last_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        row = PrinterFile(
            printer_id=p.id, remote_filename="stuck.gcode", matched_by="external"
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.delete_file",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{row.id}", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        db_session.refresh(p)
        assert p.last_error == "boom"

    def test_delete_file_falls_back_to_cached_list_when_resync_fails(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        deleted = PrinterFile(
            printer_id=p.id, remote_filename="deleted.gcode", matched_by="external"
        )
        kept = PrinterFile(
            printer_id=p.id, remote_filename="kept.gcode", matched_by="external"
        )
        db_session.add_all([deleted, kept])
        db_session.commit()
        db_session.refresh(deleted)

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.delete_file",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
            patch(
                "app.services.printer_provider.MoonrakerProvider.list_files",
                new_callable=AsyncMock,
                side_effect=ProviderError("boom", code="printer_offline"),
            ),
        ):
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{deleted.id}", headers=auth_headers
            )
        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["kept.gcode"]

    def test_hides_a_library_file_the_caller_may_not_see(
        self, client: TestClient, db_session: Session
    ) -> None:
        """A printer's file list may name a revision from a private collection.

        The remote filename is the printer's own and is always shown; what must not
        leak is the *library* row it matched to — the model's name and the original
        filename it was uploaded under both name something in a collection the caller
        has no access to.
        """
        from app.db.models import (
            FileType,
            PrinterFile,
            PrinterRole,
        )

        private = build_collection(
            db_session, name="Private", slug="private-files", path="private-files"
        )
        model = build_model(
            db_session,
            name="Secret",
            slug="secret-model",
            hash="7" * 64,
            collection_id=private.id,
        )
        artifact = build_file(
            db_session,
            model,
            path="secret.gcode",
            filename="secret.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="8" * 64,
        )
        printer = build_printer(
            db_session, name="Shared fleet", moonraker_url="http://fleet.local:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=printer.id,
                file_id=artifact.id,
                remote_filename="secret.gcode",
                matched_by="upload_history",
            )
        )
        db_session.commit()
        headers = user_headers(db_session, "fleet-operator")
        grant_printer(db_session, "fleet-operator", printer, PrinterRole.VIEW)

        response = client.get(f"/api/v1/printers/{printer.id}/files", headers=headers)

        assert response.status_code == 200, response.text
        listed = next(
            row for row in response.json() if row["remote_filename"] == "secret.gcode"
        )
        assert listed["model_id"] is None
        assert listed["model_name"] is None
        assert listed["original_filename"] is None

    def test_hides_a_library_file_whose_model_was_trashed(
        self, client: TestClient, db_session: Session
    ) -> None:
        from app.core.time import utcnow
        from app.db.models import (
            FileType,
            PrinterFile,
            PrinterRole,
        )

        model = build_model(
            db_session, name="Trashed host", slug="trashed-host", hash="6" * 64
        )
        artifact = build_file(
            db_session,
            model,
            path="trashed.gcode",
            filename="trashed.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="5" * 64,
        )
        printer = build_printer(
            db_session, name="Fleet", moonraker_url="http://fleet2.local:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=printer.id,
                file_id=artifact.id,
                remote_filename="trashed.gcode",
                matched_by="upload_history",
            )
        )
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()
        headers = user_headers(db_session, "trashed-fleet-operator")
        grant_printer(db_session, "trashed-fleet-operator", printer, PrinterRole.VIEW)

        response = client.get(f"/api/v1/printers/{printer.id}/files", headers=headers)

        # The bytes are still on the machine, but the library row is in the trash
        # and must not be surfaced through a side door.
        listed = next(
            row for row in response.json() if row["remote_filename"] == "trashed.gcode"
        )
        assert listed["model_name"] is None


class TestStartPrinterFile:
    def test_start_with_explicit_file_id_creates_vault_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):

        m = build_model(
            db_session, name="Explicit", slug="explicit-file", hash="e" * 64
        )
        f = build_file(
            db_session,
            m,
            path="/data/explicit.gcode",
            filename="explicit.gcode",
            file_type="gcode",
            version=1,
            size_bytes=10,
            sha256="f" * 64,
        )
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.refresh(f)
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "explicit.gcode", "file_id": f.id},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "vault"
        assert data["file_id"] == f.id

    def test_start_with_explicit_file_id_rejects_non_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):

        m = build_model(
            db_session, name="NonGcode", slug="non-gcode-start", hash="g" * 64
        )
        f = build_file(
            db_session,
            m,
            path="/data/model.stl",
            filename="model.stl",
            file_type="stl",
            version=1,
            size_bytes=10,
            sha256="h" * 64,
        )
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.refresh(f)
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/start",
            json={"remote_filename": "model.stl", "file_id": f.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_not_gcode"

    def test_start_non_superuser_sentinel_403(
        self, client: TestClient, db_session: Session
    ):
        headers = user_headers(db_session, "start-editor")
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        resp = client.post(
            f"/api/v1/printers/{p.id}/start",
            json={"remote_filename": "unmatched.gcode"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_start_provider_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "unmatched.gcode"},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED

    def test_start_generic_exception_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "unmatched.gcode"},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret stack" not in resp.text

    def test_refuses_to_start_when_the_provider_cannot_start_a_print(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        from dataclasses import replace

        from app.services.printer_provider import MoonrakerProvider

        printer = build_printer(
            db_session, name="No start", moonraker_url="http://nostart.local:7125"
        )
        no_start = replace(MoonrakerProvider.capabilities, supported=frozenset())

        with patch.object(MoonrakerProvider, "capabilities", no_start):
            response = client.post(
                f"/api/v1/printers/{printer.id}/start",
                headers=auth_headers,
                json={"remote_filename": "part.gcode"},
            )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "operation_not_supported_for_provider"
