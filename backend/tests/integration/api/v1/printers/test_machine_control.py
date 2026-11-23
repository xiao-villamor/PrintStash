"""Typed machine controls check live firmware state and per-printer access."""

from __future__ import annotations

import httpx
import pytest

from app.db.models import PrinterRole
from app.modules.printing import moonraker
from tests.factories import build_printer
from tests.fakes.mock_printer import create_app
from tests.integration.api.v1.printers._helpers import grant_printer, user_headers

COMMANDS = [
    ("home", {"axes": "x"}),
    ("jog", {"axis": "x", "distance": 1}),
    ("extrude", {"distance": 5}),
    ("tune", {"control": "speed", "percent": 125}),
]


@pytest.fixture
def machine(db_session, monkeypatch):
    fake_app, state = create_app()
    egress = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app))
    monkeypatch.setattr(moonraker, "get_http_client", lambda: egress)
    printer = build_printer(
        db_session, name="Workbench", moonraker_url="http://moonraker.example"
    )
    return printer, state


def _post(client, printer, headers, action, body):
    return client.post(
        f"/api/v1/printers/{printer.id}/{action}", headers=headers, json=body
    )


class TestManualExtrusion:
    @pytest.mark.parametrize(
        ("coordinates", "extrusion", "distance"),
        [
            (True, True, 5),
            (True, False, -5),
            (False, True, 5),
            (False, False, -5),
        ],
    )
    def test_extrusion_moves_requested_filament_under_existing_modes(
        self, client, auth_headers, machine, coordinates, extrusion, distance
    ):
        printer, state = machine
        state.heater_targets["extruder"] = 210
        state.position[3] = 200
        state.origin[3] = 25
        state.absolute_coordinates = coordinates
        state.absolute_extrude = extrusion
        state.extrude_factor = 0.75
        state.speed_factor = 1.25

        response = _post(
            client, printer, auth_headers, "extrude", {"distance": distance}
        )

        assert response.json() == {"ok": True}
        assert state.position[3] == pytest.approx(200 + distance)
        assert state.absolute_coordinates is coordinates
        assert state.absolute_extrude is extrusion
        assert state.extrude_factor == 0.75
        assert state.speed_factor == 1.25

    def test_extrusion_requires_provider_reported_heat(
        self, client, auth_headers, machine
    ):
        printer, state = machine

        response = _post(client, printer, auth_headers, "extrude", {"distance": -5})

        assert response.status_code == 409
        assert response.json()["detail"] == "extruder_too_cold"
        assert state.position[3] == 0

    def test_extrusion_rejects_unknown_active_tool_temperature(
        self, client, auth_headers, machine
    ):
        printer, state = machine
        state.active_extruder = "extruder1"
        state.heater_targets["extruder"] = 210

        response = _post(client, printer, auth_headers, "extrude", {"distance": 5})

        assert response.status_code == 409
        assert response.json()["detail"] == "machine_control_unavailable"


class TestPrintTuning:
    @pytest.mark.parametrize(
        ("control", "percent", "status_key", "field", "expected"),
        [
            ("fan", 0, "fan", "speed", 0),
            ("fan", 100, "fan", "speed", 1),
            ("speed", 150, "gcode_move", "speed_factor", 1.5),
            ("flow", 50, "gcode_move", "extrude_factor", 0.5),
        ],
    )
    @pytest.mark.parametrize("print_state", ["standby", "printing", "paused"])
    def test_tuning_sets_native_value(
        self,
        client,
        auth_headers,
        machine,
        control,
        percent,
        status_key,
        field,
        expected,
        print_state,
    ):
        printer, state = machine
        if print_state != "standby":
            state.start("job.gcode")
        if print_state == "paused":
            state.pause()

        response = _post(
            client,
            printer,
            auth_headers,
            "tune",
            {"control": control, "percent": percent},
        )

        assert response.json() == {"ok": True}
        assert state.status()[status_key][field] == pytest.approx(expected)

    def test_fan_tuning_requires_optional_hardware(self, client, auth_headers, machine):
        printer, state = machine
        state.has_fan = False

        response = _post(
            client, printer, auth_headers, "tune", {"control": "fan", "percent": 30}
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "part_cooling_fan_unavailable"


class TestToolheadMotion:
    @pytest.mark.parametrize(
        ("axes", "expected"), [("", "xyz"), ("X", "x"), ("yz", "yz")]
    )
    def test_home_moves_only_requested_axes(
        self, client, auth_headers, machine, axes, expected
    ):
        printer, state = machine
        state.position[:3] = [10, 20, 30]

        response = _post(client, printer, auth_headers, "home", {"axes": axes})

        assert response.json() == {"ok": True}
        assert state.status()["toolhead"]["homed_axes"] == expected
        assert state.position[:3] == [
            0 if axis in expected else (i + 1) * 10 for i, axis in enumerate("xyz")
        ]

    @pytest.mark.parametrize(("action", "body"), COMMANDS[:3])
    @pytest.mark.parametrize("print_state", ["printing", "paused"])
    def test_manual_motion_blocks_during_printing_or_pause(
        self, client, auth_headers, machine, action, body, print_state
    ):
        printer, state = machine
        state.start("job.gcode")
        if print_state == "paused":
            state.pause()

        response = _post(client, printer, auth_headers, action, body)

        assert response.status_code == 409
        assert response.json()["detail"] == "printer_not_idle"

    def test_home_refuses_firmware_reported_motion(self, client, auth_headers, machine):
        printer, state = machine
        state.idle_state = "Printing"

        response = _post(client, printer, auth_headers, "home", {})

        assert response.status_code == 409
        assert response.json()["detail"] == "printer_not_idle"

    @pytest.mark.parametrize(
        ("axis", "distance", "absolute"),
        [("x", 0.1, True), ("y", -10, False), ("z", 1, True)],
    )
    def test_jog_uses_signed_delta_on_selected_homed_axis(
        self, client, auth_headers, machine, axis, distance, absolute
    ):
        printer, state = machine
        state.position[:3] = [40, 50, 60]
        state.origin[:3] = [15, 20, 25]
        state.homed_axes = {axis}
        state.absolute_coordinates = absolute
        state.absolute_extrude = False
        state.speed_factor = 1.25
        state.extrude_factor = 0.75
        expected = state.position.copy()
        expected["xyz".index(axis)] += distance

        response = _post(
            client, printer, auth_headers, "jog", {"axis": axis, "distance": distance}
        )

        assert response.json() == {"ok": True}
        assert state.position == pytest.approx(expected)
        assert state.absolute_coordinates is absolute
        assert state.absolute_extrude is False
        assert state.speed_factor == 1.25
        assert state.extrude_factor == 0.75

    def test_jog_requires_selected_axis_to_be_homed(
        self, client, auth_headers, machine
    ):
        printer, state = machine
        state.homed_axes = {"y", "z"}

        response = _post(
            client, printer, auth_headers, "jog", {"axis": "x", "distance": 1}
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "axis_not_homed"
        assert state.position[0] == 0

    @pytest.mark.parametrize(("position", "distance"), [(250, 0.1), (0, -0.1)])
    def test_jog_refuses_crossing_machine_limits(
        self, client, auth_headers, machine, position, distance
    ):
        printer, state = machine
        state.homed_axes = {"x"}
        state.position[0] = position
        state.origin[0] = 40

        response = _post(
            client, printer, auth_headers, "jog", {"axis": "x", "distance": distance}
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "move_out_of_bounds"
        assert state.position[0] == position

    def test_firmware_rejection_leaves_motion_modes_untouched(
        self, client, auth_headers, machine
    ):
        printer, state = machine
        state.heater_targets["extruder"] = 210
        state.max_extrude_only_distance = 2
        state.absolute_coordinates = False
        state.absolute_extrude = False
        state.extrude_factor = 1.25
        state.speed_factor = 0.75

        response = _post(client, printer, auth_headers, "extrude", {"distance": 5})

        assert response.status_code == 502
        assert response.json()["detail"] == "provider_transport_error"
        assert state.position[3] == 0
        assert state.absolute_coordinates is False
        assert state.absolute_extrude is False
        assert state.extrude_factor == 1.25
        assert state.speed_factor == 0.75


class TestMachineControl:
    @pytest.mark.parametrize(("action", "body"), COMMANDS)
    def test_print_role_cannot_operate_machine(
        self, client, db_session, machine, action, body
    ):
        printer, _state = machine
        headers = user_headers(db_session, "printer-member")
        grant_printer(db_session, "printer-member", printer, PrinterRole.PRINT)

        response = _post(client, printer, headers, action, body)

        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"

    @pytest.mark.parametrize(("action", "body"), COMMANDS)
    def test_read_scope_cannot_operate_machine(
        self, client, db_session, machine, action, body
    ):
        printer, _state = machine
        headers = user_headers(db_session, "read-only-operator", scope="read")
        grant_printer(db_session, "read-only-operator", printer, PrinterRole.CONTROL)

        response = _post(client, printer, headers, action, body)

        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"

    @pytest.mark.parametrize(("action", "body"), COMMANDS)
    def test_bambu_rejects_machine_control(
        self, client, db_session, auth_headers, action, body
    ):
        printer = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            bambu_host="bambu.example",
            bambu_serial="test-serial",
            bambu_access_code="test-code",
        )

        response = _post(client, printer, auth_headers, action, body)

        assert response.status_code == 409
        assert response.json()["detail"] == "operation_not_supported_for_provider"

    @pytest.mark.parametrize(
        ("action", "body"),
        [
            ("jog", '{"axis":"xy","distance":1}'),
            ("jog", '{"axis":"z","distance":-10.01}'),
            ("jog", '{"axis":"x","distance":0.01}'),
            ("jog", '{"axis":"x","distance":0}'),
            ("jog", '{"axis":"x","distance":true}'),
            ("jog", '{"axis":"x","distance":"1"}'),
            ("jog", '{"axis":"x","distance":1e999}'),
            ("jog", '{"axis":"x","distance":1,"script":"G28"}'),
            ("extrude", '{"distance":-50.01}'),
            ("extrude", '{"distance":0}'),
            ("extrude", '{"distance":NaN}'),
            ("tune", '{"control":"chamber","percent":50}'),
            ("tune", '{"control":"fan","percent":100.01}'),
            ("tune", '{"control":"fan","percent":-0.01}'),
            ("tune", '{"control":"speed","percent":49.99}'),
            ("tune", '{"control":"flow","percent":150.01}'),
            ("tune", '{"control":"fan","percent":Infinity}'),
        ],
    )
    def test_invalid_machine_payload_is_rejected(
        self, client, auth_headers, machine, action, body
    ):
        printer, _state = machine
        response = client.post(
            f"/api/v1/printers/{printer.id}/{action}",
            content=body,
            headers={**auth_headers, "Content-Type": "application/json"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "request_validation_failed"

    @pytest.mark.parametrize(("action", "body"), COMMANDS)
    def test_machine_control_requires_ready_firmware(
        self, client, auth_headers, machine, action, body
    ):
        printer, state = machine
        state.ready = False

        response = _post(client, printer, auth_headers, action, body)

        assert response.status_code == 409
        assert response.json()["detail"] == "printer_not_ready"

    @pytest.mark.parametrize(
        ("action", "body", "section", "field"),
        [
            ("home", {}, "idle_timeout", "state"),
            ("jog", {"axis": "x", "distance": 1}, "print_stats", "state"),
            ("jog", {"axis": "x", "distance": 1}, "toolhead", "homed_axes"),
            ("jog", {"axis": "x", "distance": 1}, "toolhead", "axis_minimum"),
            ("jog", {"axis": "x", "distance": 1}, "gcode_move", "gcode_position"),
            ("jog", {"axis": "x", "distance": 1}, "gcode_move", "absolute_coordinates"),
            ("extrude", {"distance": 1}, "extruder", "can_extrude"),
            ("extrude", {"distance": 1}, "gcode_move", "absolute_extrude"),
            ("extrude", {"distance": 1}, "gcode_move", "extrude_factor"),
            (
                "tune",
                {"control": "speed", "percent": 100},
                "gcode_move",
                "speed_factor",
            ),
        ],
    )
    def test_missing_safety_telemetry_blocks_machine_control(
        self, client, auth_headers, machine, monkeypatch, action, body, section, field
    ):
        printer, state = machine
        state.homed_axes = {"x"}
        state.heater_targets["extruder"] = 210
        status = state.status()
        del status[section][field]

        def respond(request):
            assert request.method == "GET"
            assert request.url.path == "/printer/objects/query"
            return httpx.Response(200, json={"result": {"status": status}})

        egress = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        monkeypatch.setattr(moonraker, "get_http_client", lambda: egress)

        response = _post(client, printer, auth_headers, action, body)

        assert response.status_code == 409
        assert response.json()["detail"] == "machine_control_unavailable"
