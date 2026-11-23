"""Bed measurement authorization, preflight, and controller receipts."""

import httpx
import pytest

from app.modules.printing import moonraker

_MEASUREMENT = "/bed-mesh/measurement"


@pytest.fixture
def controller(monkeypatch):
    requests = []
    status = {
        "print_stats": {"state": "standby"},
        "webhooks": {"state": "ready"},
        "idle_timeout": {"state": "Ready"},
        "toolhead": {"homed_axes": "xyz"},
        "bed_mesh": {"probed_matrix": [[]]},
        "manual_probe": {"is_active": False},
        "probe": {"name": "probe", "last_query": False},
        "gcode": {"commands": {"BED_MESH_CALIBRATE": {}}},
        "configfile": {"settings": {"bed_mesh": {"probe_count": [3, 3]}}},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/printer/gcode/script"
            if failure := status.get("post_failure"):
                raise failure
            return httpx.Response(200, json={"result": "ok"})
        if failure := status.get("get_failure"):
            raise failure
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {
                        key: status[key] for key in request.url.params if key in status
                    }
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(moonraker, "get_http_client", lambda: http)
    return requests, status


class TestBedMeasurement:
    @pytest.mark.parametrize("role", ["view", "print"])
    def test_non_control_grants_cannot_measure(
        self,
        client,
        auth_headers,
        make_printer,
        make_user,
        headers_for,
        controller,
        role,
    ):
        printer = make_printer()
        viewer = make_user()
        granted = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{viewer.id}",
            headers=auth_headers,
            json={"role": role},
        )
        assert granted.status_code == 200

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=headers_for(viewer, scope="write"),
            json={"confirmed": True},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"
        assert not controller[0]

    def test_read_scope_cannot_measure(
        self, client, auth_headers, make_printer, make_user, headers_for, controller
    ):
        printer = make_printer()
        operator = make_user()
        granted = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{operator.id}",
            headers=auth_headers,
            json={"role": "control"},
        )
        assert granted.status_code == 200

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=headers_for(operator, scope="read"),
            json={"confirmed": True},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"
        assert not controller[0]

    def test_non_moonraker_printer_cannot_measure(
        self, client, auth_headers, make_printer, controller
    ):
        printer = make_printer(provider="bambu_lan")

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "operation_not_supported_for_provider"
        assert not controller[0]

    @pytest.mark.parametrize("trashed", [False, True])
    def test_missing_printer_cannot_measure(
        self, client, auth_headers, make_printer, controller, trashed
    ):
        printer_id = make_printer(trashed=True).id if trashed else 999_999

        response = client.post(
            f"/api/v1/printers/{printer_id}{_MEASUREMENT}",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 404
        assert not controller[0]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"confirmed": False},
            {"confirmed": 1},
            {"confirmed": "true"},
            {"confirmed": True, "script": "SAVE_CONFIG"},
            {"confirmed": True, "profile": "my-profile"},
        ],
    )
    def test_measurement_requires_only_explicit_confirmation(
        self, client, auth_headers, make_printer, controller, body
    ):
        printer = make_printer()

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=auth_headers,
            json=body,
        )

        assert response.status_code == 422
        assert not controller[0]

    @pytest.mark.parametrize(
        ("override", "detail"),
        [
            ({"print_stats": {"state": "printing"}}, "printer_not_idle"),
            ({"print_stats": {"state": "paused"}}, "printer_not_idle"),
            ({"idle_timeout": {"state": "Printing"}}, "printer_not_idle"),
            ({"manual_probe": {"is_active": True}}, "printer_not_idle"),
            ({"webhooks": {"state": "shutdown"}}, "printer_not_ready"),
            ({"toolhead": {"homed_axes": "xy"}}, "bed_measurement_axes_not_homed"),
            ({"toolhead": {}}, "bed_measurement_axes_not_homed"),
            ({"bed_mesh": {}}, "bed_measurement_unsupported"),
            ({"probe": {}}, "bed_measurement_unsupported"),
            ({"gcode": {"commands": {}}}, "bed_measurement_unsupported"),
            (
                {
                    "configfile": {
                        "settings": {
                            "bed_mesh": {},
                            "gcode_macro BED_MESH_CALIBRATE": {},
                        }
                    }
                },
                "bed_measurement_unsupported",
            ),
        ],
    )
    def test_rechecks_firmware_before_moving(
        self, client, auth_headers, make_printer, controller, override, detail
    ):
        requests, status = controller
        printer = make_printer()
        url = f"/api/v1/printers/{printer.id}{_MEASUREMENT}"
        available = client.get(url, headers=auth_headers)
        assert available.json() == {"supported": True}
        status.update(override)

        response = client.post(url, headers=auth_headers, json={"confirmed": True})

        assert response.status_code == 409
        assert response.json()["detail"] == detail
        assert [request.method for request in requests] == ["GET", "GET"]

    def test_manual_only_bed_mesh_is_unavailable(
        self, client, auth_headers, make_printer, controller
    ):
        _, status = controller
        del status["probe"]
        printer = make_printer()

        response = client.get(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json() == {"supported": False}

    def test_offline_printer_never_receives_calibrate(
        self, client, auth_headers, make_printer, controller
    ):
        requests, status = controller
        status["get_failure"] = httpx.ConnectError("controller unavailable")
        printer = make_printer()

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "printer_offline"
        assert [request.method for request in requests] == ["GET"]

    def test_lost_controller_response_is_never_replayed(
        self, client, auth_headers, make_printer, controller
    ):
        requests, status = controller
        status["post_failure"] = httpx.ReadTimeout("controller still probing")
        printer = make_printer()

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "bed_measurement_unknown"
        assert [request.method for request in requests] == ["GET", "POST"]
        assert requests[-1].url.params["script"] == "BED_MESH_CALIBRATE"
        assert (
            requests[-1].extensions["timeout"]["read"]
            > requests[0].extensions["timeout"]["read"]
        )

    def test_completed_measurement_with_no_grid_has_a_distinct_receipt(
        self, client, auth_headers, make_printer, controller
    ):
        requests, _ = controller
        printer = make_printer()

        response = client.post(
            f"/api/v1/printers/{printer.id}{_MEASUREMENT}",
            headers=auth_headers,
            json={"confirmed": True},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "bed_measurement_mesh_unavailable"
        assert [request.method for request in requests] == ["GET", "POST", "GET"]
