"""The inspection routes honor view grants before making read-only provider calls."""

import httpx
import pytest

from app.api.v1 import printer_inspection
from app.modules.printing import moonraker

_HISTORY = "heater-history"
_MESH = "bed-mesh"


@pytest.fixture
def controller(monkeypatch):
    requests = []
    responses = {
        "/server/temperature_store": {
            "result": {"extruder": {"temperatures": [25, 26]}}
        },
        "/printer/objects/query?bed_mesh": {"result": {"status": {"bed_mesh": {}}}},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = responses[request.url.raw_path.decode()]
        if isinstance(body, httpx.RequestError):
            raise body
        return httpx.Response(200, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(moonraker, "get_http_client", lambda: http)
    return requests, responses


class TestPrinterInspection:
    @pytest.mark.parametrize("endpoint", [_HISTORY, _MESH])
    def test_requires_authentication(self, client, make_printer, controller, endpoint):
        printer = make_printer()

        response = client.get(f"/api/v1/printers/{printer.id}/{endpoint}")

        assert response.status_code == 401
        assert not controller[0]

    @pytest.mark.parametrize("endpoint", [_HISTORY, _MESH])
    def test_denies_a_grant_for_a_different_printer(
        self,
        client,
        auth_headers,
        make_printer,
        make_user,
        headers_for,
        controller,
        endpoint,
    ):
        allowed = make_printer()
        hidden = make_printer()
        viewer = make_user()
        grant = client.put(
            f"/api/v1/printers/{allowed.id}/permissions/{viewer.id}",
            headers=auth_headers,
            json={"role": "view"},
        )
        assert grant.status_code == 200

        response = client.get(
            f"/api/v1/printers/{hidden.id}/{endpoint}",
            headers=headers_for(viewer, scope="read"),
        )

        assert response.status_code == 403
        assert not controller[0]

    @pytest.mark.parametrize("endpoint", [_HISTORY, _MESH])
    def test_excludes_trashed_printers(
        self, client, auth_headers, make_printer, controller, endpoint
    ):
        printer = make_printer(trashed=True)

        response = client.get(
            f"/api/v1/printers/{printer.id}/{endpoint}", headers=auth_headers
        )

        assert response.status_code == 404
        assert not controller[0]

    @pytest.mark.parametrize("endpoint", [_HISTORY, _MESH])
    def test_provider_without_moonraker_is_unsupported(
        self, client, auth_headers, make_printer, controller, endpoint
    ):
        printer = make_printer(provider="bambu_lan")

        response = client.get(
            f"/api/v1/printers/{printer.id}/{endpoint}", headers=auth_headers
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "operation_not_supported_for_provider"
        assert not controller[0]

    def test_missing_connection_settings_fail_before_device_io(
        self, client, auth_headers, make_printer, controller
    ):
        printer = make_printer(moonraker_url="")

        response = client.get(
            f"/api/v1/printers/{printer.id}/{_MESH}", headers=auth_headers
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "provider_credentials_missing"
        assert not controller[0]

    def test_wrong_registry_adapter_fails_before_device_io(
        self, client, auth_headers, make_printer, controller, monkeypatch
    ):
        printer = make_printer()
        monkeypatch.setattr(
            printer_inspection, "get_provider_client", lambda *args, **kw: object()
        )

        response = client.get(
            f"/api/v1/printers/{printer.id}/{_MESH}", headers=auth_headers
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "provider_config_mismatch"
        assert not controller[0]

    @pytest.mark.parametrize(
        ("endpoint", "provider_path", "expected"),
        [
            (
                _HISTORY,
                "/server/temperature_store",
                {
                    "sample_interval_ms": 1000,
                    "extruder": [
                        {"age_ms": 1000, "temperature": 25, "target": None},
                        {"age_ms": 0, "temperature": 26, "target": None},
                    ],
                    "heater_bed": [],
                },
            ),
            (_MESH, "/printer/objects/query?bed_mesh", {"state": "unconfigured"}),
        ],
    )
    def test_view_grant_reads_from_the_configured_provider(
        self,
        client,
        auth_headers,
        make_printer,
        make_user,
        headers_for,
        controller,
        endpoint,
        provider_path,
        expected,
    ):
        printer = make_printer(
            moonraker_url="http://controller.local:7125", api_key="test-key"
        )
        viewer = make_user()
        grant = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{viewer.id}",
            headers=auth_headers,
            json={"role": "view"},
        )
        assert grant.status_code == 200

        response = client.get(
            f"/api/v1/printers/{printer.id}/{endpoint}",
            headers=headers_for(viewer, scope="read"),
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == expected
        (request,) = controller[0]
        assert request.method == "GET"
        assert str(request.url) == f"http://controller.local:7125{provider_path}"
        assert request.headers["x-api-key"] == "test-key"

    @pytest.mark.parametrize(
        ("simulated", "detail"),
        [
            (
                httpx.ConnectError("private controller URL"),
                "printer_inspection_unreachable",
            ),
            ({"result": {"bad_status": {}}}, "printer_inspection_invalid_response"),
        ],
    )
    def test_provider_failure_has_a_stable_error(
        self, client, auth_headers, make_printer, controller, simulated, detail
    ):
        printer = make_printer()
        controller[1]["/printer/objects/query?bed_mesh"] = simulated

        response = client.get(
            f"/api/v1/printers/{printer.id}/{_MESH}", headers=auth_headers
        )

        assert response.status_code == 502
        assert response.json()["detail"] == detail
