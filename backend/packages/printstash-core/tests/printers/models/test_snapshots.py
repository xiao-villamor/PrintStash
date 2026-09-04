"""One immutable printer state, independent of any provider's wire protocol.

`PrinterSnapshot` is the seam that lets four incompatible printer protocols feed
one job machine, one UI, and one filament ledger. Two properties make it
trustworthy, and both are easy to break by accident.

**A legacy payload round-trips exactly.** The API's existing shape is the
Moonraker-flavoured `result.status` envelope, and callers above this layer still
read it. So a snapshot parsed from a payload must serialize back to *that same
payload*, byte for byte — including fields this version has never heard of.
Provider firmware adds fields without warning, and a snapshot that dropped them
would silently truncate what the API returns. Unknown fields therefore survive in
`extra`, frozen, and re-emerge on the way out.

**Immutability is deep and defensive.** A snapshot is passed to several consumers
and cached; a mutable nested mapping shared between them is an order-dependent
bug a long way from its cause. Nested containers are frozen, and
`to_legacy_payload` hands out a fresh mutable copy each time so a caller can edit
its own copy without touching the snapshot.

Sparseness is the third rule. A snapshot built from a payload with three fields
serializes three fields — not those three plus a dozen nulls. The distinction is
load-bearing: `"filename": null` claims the printer reported no filename, while
an absent key means it was never asked.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

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


class TestFromLegacyPayload:
    @pytest.mark.parametrize("payload", [MOONRAKER_PAYLOAD, BAMBU_PAYLOAD])
    def test_round_trips_a_payload_unchanged(self, payload: dict[str, object]) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(payload)

        # Including `future_section` and `future_print_field`, which this
        # version does not understand: firmware adds fields without warning,
        # and dropping them would truncate what the API returns.
        assert snapshot.to_legacy_payload() == payload

    @pytest.mark.parametrize("payload", [MOONRAKER_PAYLOAD, BAMBU_PAYLOAD])
    def test_hands_each_caller_its_own_mutable_copy(
        self, payload: dict[str, object]
    ) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(payload)

        emitted = snapshot.to_legacy_payload()
        emitted["mutated"] = True

        # Several consumers hold the same snapshot; one editing its copy must
        # not change what the next one reads.
        assert snapshot.to_legacy_payload() == payload

    def test_reads_the_status_out_of_the_result_envelope(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.state == "printing"

    def test_reads_a_status_object_given_directly(self) -> None:
        # Subscriptions deliver the bare status; `query_status` delivers the
        # envelope. One parser takes both.
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"print_stats": {"state": "paused"}}
        )

        assert snapshot.state == "paused"

    def test_returns_an_empty_snapshot_for_an_empty_payload(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload({})

        assert snapshot.state is None
        assert snapshot.to_legacy_payload() == {}

    def test_ignores_a_status_section_of_the_wrong_type(self) -> None:
        # A provider answering with a string where an object belongs must not
        # take the poll loop down.
        snapshot = PrinterSnapshot.from_legacy_payload({"print_stats": "unavailable"})

        assert snapshot.state is None

    def test_exposes_the_print_statistics(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.filename == "cube.gcode"
        assert snapshot.progress == 0.25
        assert snapshot.print.print_duration == 120.0
        assert snapshot.print.total_duration == 130.0
        assert snapshot.print.filament_used == 1234.5
        assert snapshot.print.message == ""

    def test_exposes_every_reported_temperature(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.bed == TemperatureSnapshot(59.5, 60.0)
        assert snapshot.extruder == TemperatureSnapshot(214.0, 215.0)

    def test_exposes_the_toolhead_state(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.position == (1.0, 2.0, 3.0, 4.0)
        assert snapshot.homed_axes == "xyz"

    def test_exposes_the_webhook_state(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.webhook_state == "ready"
        assert snapshot.webhook_message == "Printer is ready"

    def test_types_every_external_job_identifier(self) -> None:
        print_snapshot = PrinterSnapshot.from_legacy_payload(BAMBU_PAYLOAD).print

        # These are what let a print started from the vendor's own app be
        # adopted as a PrintStash job rather than an anonymous busy printer.
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

    def test_reads_a_named_temperature_sensor(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"temperature_sensor chamber": {"temperature": 31.0}}
        )

        # Chamber and other auxiliary sensors are named per printer, so the
        # section key is data rather than a known field.
        assert snapshot.temperatures["temperature_sensor chamber"].temperature == 31.0

    def test_drops_a_position_containing_something_that_is_not_a_number(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"toolhead": {"position": [1.0, "x", 3.0]}}
        )

        # A partly-numeric position is worse than none: consumers do arithmetic
        # on it.
        assert snapshot.position is None

    def test_drops_a_position_that_is_not_a_sequence(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"toolhead": {"position": "home"}}
        )

        assert snapshot.position is None

    def test_drops_a_boolean_where_a_number_belongs(self) -> None:
        # `True` is an `int` in Python, so a naive check would accept it and
        # report a temperature of 1 °C.
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"extruder": {"temperature": True}}
        )

        assert snapshot.extruder == TemperatureSnapshot(None, None)

    def test_drops_a_boolean_where_a_layer_count_belongs(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"print_stats": {"external_current_layer": True}}
        )

        assert snapshot.print.external_current_layer is None

    def test_types_the_material_slots_a_provider_reported(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {
                "material_slots": [
                    {
                        "slot_key": "ams:0:0",
                        "label": "AMS 0 tray 0",
                        "state": "loaded",
                        "material_type": "PLA",
                        "color_hex": "#FF0000",
                        "tool_key": "tool0",
                    }
                ]
            }
        )

        # The filament ledger joins on `slot_key`, so these have to survive the
        # round trip through the snapshot as typed rows, not as opaque extra.
        assert snapshot.material_slots == (
            MaterialSlotSnapshot(
                slot_key="ams:0:0",
                label="AMS 0 tray 0",
                state="loaded",
                material_type="PLA",
                color_hex="#FF0000",
                tool_key="tool0",
            ),
        )

    def test_skips_a_material_slot_missing_its_identity(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"material_slots": [{"label": "AMS 0 tray 0", "state": "loaded"}, "bad"]}
        )

        # Without a slot key there is nothing for the ledger to join on, and a
        # placeholder key would merge two unrelated trays.
        assert snapshot.material_slots == ()

    def test_types_the_material_tools_a_provider_reported(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {
                "material_tools": [
                    {
                        "tool_key": "tool0",
                        "label": "Tool 0",
                        "nozzle_diameter_mm": 0.4,
                    }
                ]
            }
        )

        assert snapshot.material_tools == (
            ToolSnapshot(tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4),
        )

    def test_skips_a_material_tool_missing_its_identity(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(
            {"material_tools": [{"nozzle_diameter_mm": 0.4}, "bad"]}
        )

        assert snapshot.material_tools == ()

    def test_ignores_material_tools_that_are_not_a_sequence(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload({"material_tools": "none"})

        assert snapshot.material_tools == ()

    def test_drops_a_state_that_is_not_a_string(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload({"print_stats": {"state": 7}})

        assert snapshot.state is None


class TestImmutability:
    def test_refuses_a_write_to_an_unknown_status_section(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        with pytest.raises(TypeError):
            snapshot.extra["future_section"] = "changed"  # type: ignore[index]

    def test_refuses_a_write_nested_inside_an_unknown_field(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        # Freezing only the top level would leave a mutable object shared
        # between every consumer of the snapshot.
        with pytest.raises(TypeError):
            snapshot.print.extra["future_print_field"]["items"] = ()  # type: ignore[index]

    def test_refuses_a_write_to_a_snapshot_field(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        with pytest.raises(FrozenInstanceError):
            snapshot.print = PrintSnapshot()  # type: ignore[misc]

    def test_freezes_a_temperature_mapping_given_at_construction(self) -> None:
        temperatures = {"extruder": TemperatureSnapshot(210, 215)}

        snapshot = PrinterSnapshot(temperatures=temperatures)
        temperatures["heater_bed"] = TemperatureSnapshot(60, 60)

        # Copied, not referenced: the caller's dict must not keep changing the
        # snapshot after it was built.
        assert set(snapshot.temperatures) == {"extruder"}
        assert isinstance(snapshot.temperatures, MappingProxyType)

    def test_replaces_an_unexpected_object_with_a_stable_representation(self) -> None:
        class Provider:
            def __repr__(self) -> str:
                return "<provider object>"

        snapshot = PrinterSnapshot.from_legacy_payload(
            {"provider_extension": {"handle": Provider()}}
        )

        # Legacy payloads are JSON-shaped in production. Keeping an arbitrary
        # object by value would expose something mutable through a supposedly
        # frozen snapshot.
        assert snapshot.extra["provider_extension"]["handle"] == "<provider object>"


class TestToLegacyPayload:
    def test_emits_the_canonical_shape_for_a_snapshot_built_in_code(self) -> None:
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

    def test_omits_a_field_that_was_never_reported(self) -> None:
        payload = PrinterSnapshot(
            print=PrintSnapshot(state="standby")
        ).to_legacy_payload()

        # `"filename": null` claims the printer reported no filename; an absent
        # key means it was never asked. Consumers distinguish the two.
        assert payload == {"print_stats": {"state": "standby"}}

    def test_omits_every_section_for_an_empty_snapshot(self) -> None:
        assert PrinterSnapshot().to_legacy_payload() == {}

    def test_emits_the_homed_axes_alongside_the_position(self) -> None:
        payload = PrinterSnapshot(position=(1, 2), homed_axes="xy").to_legacy_payload()

        assert payload["toolhead"] == {"position": [1, 2], "homed_axes": "xy"}

    def test_emits_the_webhook_state(self) -> None:
        payload = PrinterSnapshot(
            webhook_state="ready", webhook_message="Printer is ready"
        ).to_legacy_payload()

        assert payload["webhooks"] == {
            "state": "ready",
            "state_message": "Printer is ready",
        }

    def test_emits_material_slots_as_json_ready_mappings(self) -> None:
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
            )
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

    def test_omits_the_optional_parts_of_an_unfilled_slot(self) -> None:
        snapshot = PrinterSnapshot(
            material_slots=(
                MaterialSlotSnapshot(
                    slot_key="external", label="External", state="empty"
                ),
            )
        )

        assert snapshot.to_legacy_payload()["material_slots"] == [
            {"slot_key": "external", "label": "External", "state": "empty"}
        ]

    def test_emits_material_tools_as_json_ready_mappings(self) -> None:
        snapshot = PrinterSnapshot(
            material_tools=(
                ToolSnapshot(tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4),
            )
        )

        assert snapshot.to_legacy_payload()["material_tools"] == [
            {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
        ]


class TestRawPayload:
    def test_returns_the_payload_it_was_parsed_from(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        assert snapshot.raw_payload["request_id"] == "abc"

    def test_is_immutable(self) -> None:
        snapshot = PrinterSnapshot.from_legacy_payload(MOONRAKER_PAYLOAD)

        with pytest.raises(TypeError):
            snapshot.raw_payload["mutated"] = True  # type: ignore[index]

    def test_derives_a_payload_for_a_snapshot_built_in_code(self) -> None:
        snapshot = PrinterSnapshot(print=PrintSnapshot(state="standby"))

        # No original payload exists, so one is produced from the fields — the
        # accessor must not return `None` and make callers branch.
        assert snapshot.raw_payload == {"print_stats": {"state": "standby"}}


class TestMaterialSlotSnapshot:
    def test_defaults_every_optional_detail_to_absent(self) -> None:
        slot = MaterialSlotSnapshot(
            slot_key="external", label="External", state="empty"
        )

        assert slot.material_type is None
        assert slot.material_brand is None
        assert slot.external_spool_id is None


class TestToolSnapshot:
    def test_defaults_the_nozzle_diameter_to_absent(self) -> None:
        # A printer that does not report its nozzle must not appear to have a
        # 0 mm one.
        assert ToolSnapshot(tool_key="tool0", label="Tool 0").nozzle_diameter_mm is None


class TestTemperatureSnapshot:
    def test_defaults_both_readings_to_absent(self) -> None:
        assert TemperatureSnapshot() == TemperatureSnapshot(None, None)
