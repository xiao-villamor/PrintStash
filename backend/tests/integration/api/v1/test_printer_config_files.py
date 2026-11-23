"""Source config edits respect revisions, scope, and the device file manager."""

import httpx
import pytest

from app.db.models import PrinterRole
from app.modules.printing import moonraker
from app.schemas.printer_config_files import MAX_CONFIG_BYTES, MAX_CONFIG_REQUEST_BYTES
from tests.factories import build_printer
from tests.fakes.mock_printer import create_app
from tests.integration.api.v1.printers._helpers import grant_printer, user_headers

FILE = "includes/disposable.cfg"


@pytest.fixture
def remote(db_session, monkeypatch):
    app, state = create_app(api_key="emulator-key")
    egress = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    monkeypatch.setattr(moonraker, "get_http_client", lambda: egress)
    printer = build_printer(
        db_session,
        name="Config source",
        moonraker_url="http://moonraker.example",
        api_key="emulator-key",
    )
    return printer, state


def _url(printer, endpoint="file"):
    return f"/api/v1/printers/{printer.id}/config/{endpoint}"


def _open(client, printer, headers, path=FILE):
    return client.get(_url(printer), params={"path": path}, headers=headers)


def _save(client, printer, headers, base, content, path=FILE):
    return client.put(
        _url(printer),
        params={"path": path},
        headers=headers,
        json={"base_revision": base, "content": content},
    )


class TestPrinterConfigFiles:
    def test_lists_only_supported_config_paths(self, client, auth_headers, remote):
        printer, state = remote
        state.config_files["backup.json"] = b"{}"
        state.config_files[".git/broken.cfg"] = b"# hidden"
        response = client.get(_url(printer, "files"), headers=auth_headers)
        assert response.status_code == 200
        assert {row["path"]: row["writable"] for row in response.json()["files"]} == {
            "printer.cfg": True,
            FILE: True,
            "includes/read-only.conf": False,
        }

    @pytest.mark.parametrize(
        ("method", "endpoint"), [("GET", "files"), ("GET", "file"), ("PUT", "file")]
    )
    def test_config_source_requires_printer_admin(
        self, client, db_session, remote, method, endpoint
    ):
        printer, _ = remote
        headers = user_headers(db_session, "operator")
        grant_printer(db_session, "operator", printer, PrinterRole.CONTROL)
        response = client.request(
            method, _url(printer, endpoint), params={"path": FILE}, headers=headers
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"

    def test_save_requires_write_scope(self, client, db_session, remote):
        printer, _ = remote
        headers = user_headers(db_session, "readonly-admin", scope="read")
        grant_printer(db_session, "readonly-admin", printer, PrinterRole.ADMIN)
        stored = _open(client, printer, headers).json()
        response = _save(client, printer, headers, stored["revision"], "# denied")
        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"

    def test_config_source_requires_authentication(self, client, remote):
        printer, _ = remote
        assert client.get(_url(printer, "files")).status_code == 401

    def test_config_source_is_unsupported_on_bambu(
        self, client, db_session, auth_headers
    ):
        printer = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            bambu_host="bambu.example",
            bambu_serial="test-serial",
            bambu_access_code="test-code",
        )
        response = client.get(_url(printer, "files"), headers=auth_headers)
        assert response.status_code == 409
        assert response.json()["detail"] == "operation_not_supported_for_provider"

    @pytest.mark.parametrize(
        "path",
        [
            "../printer.cfg",
            "includes/../printer.cfg",
            "/printer.cfg",
            "includes//disposable.cfg",
            "includes\\disposable.cfg",
            "includes/%2e%2e/printer.cfg",
            "http://other/config.cfg",
            "includes/disposable.cfg?redirect",
            'includes/"quoted".cfg',
            "printer.gcode",
        ],
    )
    def test_invalid_paths_cannot_download_or_upload(
        self, client, auth_headers, remote, path
    ):
        printer, state = remote
        before = state.config_files.copy()
        assert _open(client, printer, auth_headers, path).status_code == 422
        response = _save(client, printer, auth_headers, "0" * 64, "# denied", path)
        assert response.status_code == 422
        assert state.config_files == before

    def test_save_does_not_create_absent_file(self, client, auth_headers, remote):
        printer, state = remote
        response = _save(
            client, printer, auth_headers, "0" * 64, "# new", "includes/new.cfg"
        )
        assert response.status_code == 404
        assert "includes/new.cfg" not in state.config_files

    @pytest.mark.parametrize("root_read_only", [True, False])
    def test_read_only_file_stays_readable(
        self, client, auth_headers, remote, root_read_only
    ):
        printer, state = remote
        state.config_read_only = root_read_only
        path = FILE if root_read_only else "includes/read-only.conf"
        stored = _open(client, printer, auth_headers, path).json()
        assert stored["writable"] is False
        response = _save(
            client, printer, auth_headers, stored["revision"], "# denied", path
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "printer_config_read_only"

    def test_stale_revision_does_not_overwrite_external_edit(
        self, client, auth_headers, remote
    ):
        printer, state = remote
        stored = _open(client, printer, auth_headers).json()
        state.config_files[FILE] = b"# external\r\n"
        response = _save(client, printer, auth_headers, stored["revision"], "# local")
        assert response.status_code == 409
        assert response.json()["detail"] == "printer_config_conflict"
        assert state.config_files[FILE] == b"# external\r\n"

    def test_lost_upload_ack_requires_review(self, client, auth_headers, remote):
        printer, state = remote
        state.config_save_unconfirmed = True
        stored = _open(client, printer, auth_headers).json()
        response = _save(client, printer, auth_headers, stored["revision"], "# applied")
        assert response.status_code == 502
        assert response.json()["detail"] == "printer_config_save_unconfirmed"
        assert _open(client, printer, auth_headers).json()["content"] == "# applied"

    @pytest.mark.parametrize("binary", [b"\xff\xfe", b"\x00\x03"])
    def test_binary_config_is_rejected(self, client, auth_headers, remote, binary):
        printer, state = remote
        state.config_files[FILE] = binary
        response = _open(client, printer, auth_headers)
        assert response.status_code == 422
        assert response.json()["detail"] == "printer_config_not_text"

    def test_save_rejects_oversized_utf8_bytes(self, client, auth_headers, remote):
        printer, _ = remote
        response = _save(
            client, printer, auth_headers, "0" * 64, "é" * (MAX_CONFIG_BYTES // 2 + 1)
        )
        assert response.status_code == 413

    def test_save_request_body_has_an_independent_byte_limit(
        self, client, auth_headers, remote
    ):
        printer, _ = remote
        response = client.put(
            _url(printer),
            params={"path": FILE},
            headers=auth_headers,
            content=b" " * (MAX_CONFIG_REQUEST_BYTES + 1),
        )
        assert response.status_code == 413

    def test_validation_never_echoes_config_contents(
        self, client, auth_headers, remote
    ):
        printer, _ = remote
        response = _save(
            client, printer, auth_headers, "invalid revision", "# sample secret"
        )
        assert response.status_code == 422
        assert "# sample secret" not in response.text

    def test_file_stream_limit_applies_even_when_listing_size_is_stale(
        self, client, auth_headers, remote, monkeypatch
    ):
        printer, _ = remote

        def respond(request):
            endpoint = request.url.path
            if endpoint == "/server/files/roots":
                return httpx.Response(
                    200, json={"result": [{"name": "config", "permissions": "rw"}]}
                )
            if endpoint == "/server/files/list":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "path": FILE,
                                "permissions": "rw",
                                "modified": 1,
                                "size": 2,
                            }
                        ]
                    },
                )
            if endpoint == "/server/files/directory":
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "files": [
                                {
                                    "filename": "disposable.cfg",
                                    "permissions": "rw",
                                }
                            ]
                        }
                    },
                )
            assert endpoint == f"/server/files/config/{FILE}"
            return httpx.Response(200, content=b"#" * (MAX_CONFIG_BYTES + 1))

        egress = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(moonraker, "get_http_client", lambda: egress)

        response = _open(client, printer, auth_headers)

        assert response.status_code == 413
        assert response.json()["detail"] == "printer_config_too_large"
