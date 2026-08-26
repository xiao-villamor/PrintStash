"""Defends printer files at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterFile,
    Session,
    TestClient,
    User,
    _user_headers,
    patch,
    select,
)


class TestPrinterFiles:
    def _setup_file(self, db_session: Session):
        from app.db.models import File, Model

        m = Model(name="Bracket", slug="bracket", hash="b" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="c" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        return m, f

    def test_list_printer_files_returns_matched_and_external(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import PrinterFile

        m, f = self._setup_file(db_session)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(f"/api/v1/printers/{p.id}/files/sync")
        assert resp.status_code == 401

    def test_sync_printer_files_uses_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._setup_file(db_session)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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

    def test_delete_printer_file_removes_remote_and_resyncs_inventory(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        resp = client.post(f"/api/v1/printers/{p.id}/files/sync", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_non_superuser_cannot_list_printer_files(
        self, client: TestClient, db_session: Session
    ):
        headers = _user_headers(db_session, "viewer")
        viewer = db_session.exec(select(User).where(User.username == "viewer")).one()
        allowed = Collection(name="Allowed", slug="allowed", path="allowed")
        denied = Collection(name="Denied", slug="denied", path="denied")
        db_session.add_all([allowed, denied])
        db_session.commit()
        db_session.refresh(allowed)
        db_session.refresh(denied)
        db_session.add(
            CollectionPermission(
                user_id=viewer.id,
                collection_id=allowed.id,
                role=CollectionRole.VIEW,
            )
        )
        allowed_model = Model(
            name="Allowed model",
            slug="allowed-model",
            hash="1" * 64,
            collection_id=allowed.id,
        )
        denied_model = Model(
            name="Denied model",
            slug="denied-model",
            hash="2" * 64,
            collection_id=denied.id,
        )
        db_session.add_all([allowed_model, denied_model])
        db_session.commit()
        db_session.refresh(allowed_model)
        db_session.refresh(denied_model)
        allowed_file = File(
            model_id=allowed_model.id,
            path="/data/allowed.gcode",
            original_filename="allowed.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="3" * 64,
        )
        denied_file = File(
            model_id=denied_model.id,
            path="/data/denied.gcode",
            original_filename="denied.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="4" * 64,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
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
        headers = _user_headers(db_session, "editor")
        collection = Collection(name="Private", slug="private", path="private")
        db_session.add(collection)
        db_session.commit()
        db_session.refresh(collection)
        model = Model(
            name="Private model",
            slug="private-model",
            hash="5" * 64,
            collection_id=collection.id,
        )
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file_row = File(
            model_id=model.id,
            path="/data/private.gcode",
            original_filename="private.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="6" * 64,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
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
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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
        from app.db.models import SENTINEL_FILE_HASH, File, PrinterFile

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
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
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

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
