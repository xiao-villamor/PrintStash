"""Integration tests for printer flows using a real local HTTP server.

These tests intentionally avoid patching the provider/client methods. They run
against a small in-process Moonraker-compatible HTTP server so the API exercises
real HTTP requests, multipart upload, local storage, database writes, and router
error handling without requiring physical printer hardware in CI.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Iterator
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import File, FileType, Model, PrintJob, Printer, PrinterFile


@dataclass
class MoonrakerServerState:
    info_requests: int = 0
    status_requests: int = 0
    upload_requests: int = 0
    start_requests: list[str] = field(default_factory=list)
    control_requests: list[str] = field(default_factory=list)
    last_upload_body: bytes = b""
    last_upload_content_type: str = ""
    list_files_status: int = 200
    remote_files: list[dict] = field(
        default_factory=lambda: [{"path": "existing.gcode", "size": 42}]
    )


class MoonrakerHandler(BaseHTTPRequestHandler):
    server: "MoonrakerHTTPServer"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _json(self, status_code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/printer/info":
            self.server.state.info_requests += 1
            self._json(200, {"result": {"hostname": "real-test-moonraker"}})
            return
        if parsed.path == "/printer/objects/query":
            self.server.state.status_requests += 1
            self._json(
                200,
                {
                    "result": {
                        "status": {
                            "print_stats": {
                                "state": "standby",
                                "filename": "",
                            },
                            "webhooks": {
                                "state": "ready",
                                "state_message": "Ready",
                            },
                        }
                    }
                },
            )
            return
        if parsed.path == "/server/files/list":
            if self.server.state.list_files_status != 200:
                self._json(
                    self.server.state.list_files_status,
                    {"error": "moonraker_list_failed"},
                )
                return
            self._json(200, {"result": self.server.state.remote_files})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/server/files/upload":
            size = int(self.headers.get("Content-Length", "0"))
            self.server.state.upload_requests += 1
            self.server.state.last_upload_content_type = self.headers.get(
                "Content-Type", ""
            )
            self.server.state.last_upload_body = self.rfile.read(size)
            self._json(200, {"result": {"item": {"path": "uploaded.gcode"}}})
            return
        if parsed.path == "/printer/print/start":
            filename = parse_qs(parsed.query).get("filename", [""])[0]
            self.server.state.start_requests.append(filename)
            self._json(200, {"result": "ok"})
            return
        if parsed.path in {
            "/printer/print/pause",
            "/printer/print/resume",
            "/printer/print/cancel",
        }:
            self.server.state.control_requests.append(parsed.path.rsplit("/", 1)[-1])
            self._json(200, {"result": "ok"})
            return
        self._json(404, {"error": "not_found"})


class MoonrakerHTTPServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), MoonrakerHandler)
        self.state = MoonrakerServerState()

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


@contextmanager
def moonraker_server() -> Iterator[MoonrakerHTTPServer]:
    server = MoonrakerHTTPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _stored_gcode(
    db_session: Session,
    tmp_path: Path,
    *,
    name: str = "bracket.gcode",
    body: bytes = b"G28\nG1 X1 Y1\n",
) -> File:
    model = Model(name="Bracket", slug="real-bracket", hash="r" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    path = tmp_path / name
    path.write_bytes(body)
    file_row = File(
        model_id=model.id,
        path=str(path),
        original_filename=name,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=len(body),
        sha256="a" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)
    return file_row


def test_diagnostics_hits_real_moonraker_http_server(
    client: TestClient,
    db_session: Session,
) -> None:
    with moonraker_server() as server:
        printer = Printer(name="Real Moonraker", moonraker_url=server.base_url)
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        resp = client.get(f"/api/v1/printers/{printer.id}/diagnostics")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["support_level"] == "stable"
        assert [check["name"] for check in body["checks"]] == [
            "configuration",
            "provider_info",
            "live_status",
        ]
        assert server.state.info_requests == 1
        assert server.state.status_requests == 1


def test_send_to_printer_uploads_real_file_and_records_inventory(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    file_row = _stored_gcode(db_session, tmp_path)
    with moonraker_server() as server:
        printer = Printer(name="Real Moonraker", moonraker_url=server.base_url)
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        resp = client.post(
            f"/api/v1/printers/{printer.id}/send",
            json={
                "file_id": file_row.id,
                "start_print": True,
                "remote_filename": "jobs/bracket-release.gcode",
            },
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "started"
        assert server.state.upload_requests == 1
        assert "multipart/form-data" in server.state.last_upload_content_type
        assert b'filename="jobs/bracket-release.gcode"' in server.state.last_upload_body
        assert b"name=\"print\"" in server.state.last_upload_body
        assert b"true" in server.state.last_upload_body
        assert b"G28\nG1 X1 Y1\n" in server.state.last_upload_body

        job = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).one()
        assert job.file_id == file_row.id
        assert job.remote_filename == "jobs/bracket-release.gcode"

        inventory = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == printer.id)
        ).one()
        assert inventory.file_id == file_row.id
        assert inventory.remote_filename == "jobs/bracket-release.gcode"
        assert inventory.matched_by == "upload_history"


def test_start_existing_printer_file_calls_real_moonraker_start_endpoint(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    file_row = _stored_gcode(db_session, tmp_path, name="start-me.gcode")
    with moonraker_server() as server:
        printer = Printer(name="Real Moonraker", moonraker_url=server.base_url)
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        db_session.add(
            PrinterFile(
                printer_id=printer.id,
                file_id=file_row.id,
                remote_filename="folder/start-me.gcode",
                matched_by="filename",
            )
        )
        db_session.commit()

        resp = client.post(
            f"/api/v1/printers/{printer.id}/start",
            json={"remote_filename": "folder/start-me.gcode"},
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["source"] == "vault"
        assert server.state.start_requests == ["folder/start-me.gcode"]
        job = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).one()
        assert job.file_id == file_row.id
        assert job.remote_filename == "folder/start-me.gcode"


def test_sync_printer_files_uses_real_provider_list_response(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    with moonraker_server() as server:
        server.state.remote_files = [
            {"path": "folder/from-printer.gcode", "size": 321},
            {"path": "other/external.gcode", "size": 654},
        ]
        printer = Printer(name="Real Moonraker", moonraker_url=server.base_url)
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        resp = client.post(
            f"/api/v1/printers/{printer.id}/files/sync",
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.text
        assert [row["remote_filename"] for row in resp.json()] == [
            "folder/from-printer.gcode",
            "other/external.gcode",
        ]
        rows = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == printer.id)
        ).all()
        assert {row.remote_filename for row in rows} == {
            "folder/from-printer.gcode",
            "other/external.gcode",
        }


def test_sync_printer_files_reports_real_provider_http_failure(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    with moonraker_server() as server:
        server.state.list_files_status = 503
        printer = Printer(name="Failing Moonraker", moonraker_url=server.base_url)
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        resp = client.post(
            f"/api/v1/printers/{printer.id}/files/sync",
            headers=auth_headers,
        )

        assert resp.status_code == 502
        assert resp.json() == {"detail": "provider_transport_error"}
        db_session.refresh(printer)
        assert "moonraker 503" in (printer.last_error or "")
