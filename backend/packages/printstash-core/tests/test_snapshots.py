from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from printstash_core.printers import (
    MaterialSlotSnapshot,
    PrinterSnapshot,
    PrintSnapshot,
    TemperatureSnapshot,
    ToolSnapshot,
)

MOONRAKER_PAYLOAD = {
    "result": {
        "status": {
            "print_stats": {
                "state": "printing",
                "filename": "cube.gcode",
                "print_duration": 120.0,
                "total_duration": 130.0,
                "filament_used": 1234.5,
                "message": "",
                "future_print_field": {"items": [1, 2]},
            },
            "virtual_sdcard": {
                "progress": 0.25,
                "file_position": 250,
                "file_size": 1000,
                "future_storage_field": True,
            },
            "heater_bed": {"temperature": 59.5, "target": 60.0},
            "extruder": {"temperature": 214.0, "target": 215.0},
            "toolhead": {
                "position": [1.0, 2.0, 3.0, 4.0],
                "homed_axes": "xyz",
            },
            "webhooks": {
                "state": "ready",
                "state_message": "Printer is ready",
            },
            "future_section": {"nested": ["preserved"]},
        },
        "eventtime": 123.5,
    },
    "request_id": "abc",
}


BAMBU_PAYLOAD = {
    "result": {
        "status": {
            "print_stats": {
                "state": "printing",
                "filename": "plate_1.gcode",
                "message": "",
                "external_display_name": "Benchy",
                "external_task_id": "task-42",
                "external_subtask_id": "subtask-7",
                "external_project_id": "project-3",
                "external_profile_id": "profile-2",
                "external_gcode_file": "/cache/plate_1.gcode",
                "external_artifact_path": "ftps://SERIAL/cache/benchy.3mf",
                "external_plate_index": 1,
                "external_current_layer": 8,
                "external_total_layers": 120,
                "external_nozzle_diameter": 0.4,
            },
            "virtual_sdcard": {"progress": 0.42},
        }
    }
}


@pytest.mark.parametrize("payload", [MOONRAKER_PAYLOAD, BAMBU_PAYLOAD])
def test_legacy_payload_round_trip_is_exact_and_defensive(
    payload: dict[str, object],
) -> None:
    snapshot = PrinterSnapshot.from_legacy_payload(payload)
    original = snapshot.to_legacy_payload()

    assert original == payload
    original["mutated"] = True
    assert snapshot.to_legacy_payload() == payload


def test_snapshot_exposes_characterized_fields() -> None:
    snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

    assert snapshot.state == "printing"
    assert snapshot.filename == "cube.gcode"
    assert snapshot.progress == 0.25
    assert snapshot.print.print_duration == 120.0
    assert snapshot.print.total_duration == 130.0
    assert snapshot.print.filament_used == 1234.5
    assert snapshot.print.message == ""
    assert snapshot.bed == TemperatureSnapshot(59.5, 60.0)
    assert snapshot.extruder == TemperatureSnapshot(214.0, 215.0)
    assert snapshot.position == (1.0, 2.0, 3.0, 4.0)
    assert snapshot.homed_axes == "xyz"
    assert snapshot.webhook_state == "ready"
    assert snapshot.webhook_message == "Printer is ready"


def test_external_metadata_is_typed() -> None:
    print_snapshot = PrinterSnapshot.from_legacy_payload(BAMBU_PAYLOAD).print

    assert print_snapshot.external_display_name == "Benchy"
    assert print_snapshot.external_task_id == "task-42"
    assert print_snapshot.external_subtask_id == "subtask-7"
    assert print_snapshot.external_project_id == "project-3"
    assert print_snapshot.external_profile_id == "profile-2"
    assert print_snapshot.external_gcode_file == "/cache/plate_1.gcode"
    assert print_snapshot.external_artifact_path.endswith("benchy.3mf")
    assert print_snapshot.external_plate_index == 1
    assert print_snapshot.external_current_layer == 8
    assert print_snapshot.external_total_layers == 120
    assert print_snapshot.external_nozzle_diameter == 0.4


def test_unknown_fields_are_deeply_immutable() -> None:
    snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

    with pytest.raises(TypeError):
        snapshot.extra["future_section"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.print.extra["future_print_field"]["items"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        snapshot.print = PrintSnapshot()  # type: ignore[misc]


def test_sparse_direct_payload_round_trips_without_adding_null_fields() -> None:
    payload = {
        "print_stats": {"state": "standby"},
        "temperature_sensor chamber": {"temperature": 31.0},
        "provider_extension": {"job_id": 42},
    }

    snapshot = PrinterSnapshot.from_legacy_payload(payload)

    assert snapshot.to_legacy_payload() == payload
    assert snapshot.temperatures["temperature_sensor chamber"].temperature == 31.0


def test_manually_created_snapshot_has_canonical_legacy_shape() -> None:
    snapshot = PrinterSnapshot(
        print=PrintSnapshot(state="printing", filename="cube.gcode", progress=0.5),
        temperatures={"extruder": TemperatureSnapshot(210, 215)},
        position=(1, 2, 3),
    )

    assert snapshot.to_legacy_payload() == {
        "print_stats": {"state": "printing", "filename": "cube.gcode"},
        "virtual_sdcard": {"progress": 0.5},
        "extruder": {"temperature": 210, "target": 215},
        "toolhead": {"position": [1, 2, 3]},
    }


def test_material_slots_and_tools_are_typed_and_round_trip() -> None:
    snapshot = PrinterSnapshot(
        material_slots=(
            MaterialSlotSnapshot(
                slot_key="ams:0:0",
                label="AMS 0 tray 0",
                state="loaded",
                material_type="PLA",
                color_hex="#FF0000",
                tool_key="tool0",
            ),
        ),
        material_tools=(
            ToolSnapshot(tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4),
        ),
    )

    assert snapshot.to_legacy_payload()["material_slots"] == [
        {
            "slot_key": "ams:0:0",
            "label": "AMS 0 tray 0",
            "state": "loaded",
            "material_type": "PLA",
            "color_hex": "#FF0000",
            "tool_key": "tool0",
        }
    ]
    assert snapshot.to_legacy_payload()["material_tools"] == [
        {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
    ]
