"""Turning Bambu's device reports into the neutral status shape.

PrintStash normalizes every provider onto one Moonraker-shaped payload, so this
translation is where a Bambu print becomes indistinguishable from a Klipper one
to everything above it: the job state machine, the UI, the filament ledger. Two
properties matter more than the field mapping itself.

The first is **state vocabulary**. Bambu says `FINISH` where the rest of the
system says `complete`; a state that fell through untranslated would leave a
finished job printing forever and its spool debited indefinitely. The mapping is
pinned case by case.

The second is **AMS accumulation**. Bambu sends the full AMS tray table once and
then only *deltas*, so the client keeps the last known slot state and merges each
report into it. That makes `_normalize_status` stateful, which is unusual for a
normalizer and easy to break: a merge that replaced instead of updating would
make every spool but the one that just changed vanish from the UI, and a
malformed report would empty the whole panel.

`_normalize_project_request` is the artifact-capture hint. It is the only place
the printer tells us where the bytes of an externally-started print live.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import SERIAL, make_client

# One AMS unit with a loaded red PLA tray and an empty second tray.
AMS_REPORT = {
    "ams": {
        "ams": [
            {
                "id": "0",
                "tray": [
                    {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"},
                    {"id": "1", "tray_type": "", "tray_color": ""},
                ],
            }
        ]
    }
}


def slots_by_key(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["slot_key"]: row for row in status["material_slots"]}


class TestNormalizeStatus:
    def test_maps_the_reported_job_fields_into_the_envelope(self) -> None:
        status = make_client()._normalize_status(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "mc_percent": 42,
                    "gcode_file": "/cache/plate_1.gcode",
                }
            }
        )

        assert status["print_stats"]["state"] == "printing"
        assert status["print_stats"]["filename"] == "plate_1.gcode"
        assert status["virtual_sdcard"] == {"progress": 0.42}

    def test_carries_every_external_job_identifier(self) -> None:
        status = make_client()._normalize_status(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "gcode_file": "/cache/plate_1.gcode",
                    "print_error": "",
                    "subtask_name": "Benchy",
                    "task_id": "task-42",
                    "subtask_id": "subtask-7",
                    "project_id": "project-3",
                    "profile_id": "profile-2",
                    "plate_num": 1,
                    "layer_num": 8,
                    "total_layer_num": 120,
                    "nozzle_diameter": 0.4,
                }
            }
        )

        # These are what let a print started from Bambu Studio be adopted as a
        # PrintStash job rather than appearing as an anonymous "busy" printer.
        assert status["print_stats"] == {
            "state": "printing",
            "filename": "plate_1.gcode",
            "message": "",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_subtask_id": "subtask-7",
            "external_project_id": "project-3",
            "external_profile_id": "profile-2",
            "external_gcode_file": "/cache/plate_1.gcode",
            "external_plate_index": 1,
            "external_current_layer": 8,
            "external_total_layers": 120,
            "external_nozzle_diameter": 0.4,
        }

    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("IDLE", "standby"),
            ("PREPARE", "standby"),
            ("SLICING", "standby"),
            ("RUNNING", "printing"),
            ("PAUSE", "paused"),
            ("FINISH", "complete"),
            ("FAILED", "error"),
        ],
    )
    def test_translates_every_bambu_state(self, raw: str, normalized: str) -> None:
        status = make_client()._normalize_status({"print": {"gcode_state": raw}})

        # An untranslated terminal state leaves a finished job "printing" and
        # its spool debited forever.
        assert status["print_stats"] == {"state": normalized}

    def test_passes_an_unknown_state_through_lower_cased(self) -> None:
        status = make_client()._normalize_status({"print": {"gcode_state": "CUSTOM"}})

        # A firmware update adding a state must not crash the poller; the job
        # machine treats an unknown state as "not one of the terminal ones".
        assert status["print_stats"] == {"state": "custom"}

    def test_returns_nothing_for_an_empty_report(self) -> None:
        assert make_client()._normalize_status({}) == {}

    def test_ignores_an_empty_state_string(self) -> None:
        assert make_client()._normalize_status({"print": {"gcode_state": ""}}) == {}

    def test_falls_back_to_the_subtask_name_when_no_file_is_reported(self) -> None:
        status = make_client()._normalize_status(
            {"print": {"gcode_state": "RUNNING", "subtask_name": "Benchy"}}
        )

        assert status["print_stats"]["filename"] == "Benchy"

    def test_falls_back_to_the_project_id_when_there_is_no_name_either(self) -> None:
        status = make_client()._normalize_status(
            {"print": {"gcode_state": "RUNNING", "project_id": "project-3"}}
        )

        assert status["print_stats"]["filename"] == "project-3"

    def test_reduces_a_windows_style_path_to_its_filename(self) -> None:
        status = make_client()._normalize_status(
            {"print": {"gcode_file": "\\cache\\plate_1.gcode"}}
        )

        assert status["print_stats"]["filename"] == "plate_1.gcode"

    @pytest.mark.parametrize(
        ("percent", "progress"), [(0, 0.0), (100, 1.0), (-5, 0.0), (140, 1.0)]
    )
    def test_clamps_progress_into_the_unit_range(
        self, percent: int, progress: float
    ) -> None:
        status = make_client()._normalize_status({"print": {"mc_percent": percent}})

        # A percentage outside 0-100 would otherwise render as a progress bar
        # running backwards or past its end.
        assert status["virtual_sdcard"] == {"progress": progress}

    def test_omits_progress_when_the_percentage_is_not_a_number(self) -> None:
        status = make_client()._normalize_status({"print": {"mc_percent": "N/A"}})

        assert "virtual_sdcard" not in status

    def test_reports_the_nozzle_as_a_tool(self) -> None:
        status = make_client()._normalize_status({"print": {"nozzle_diameter": "0.4"}})

        assert status["material_tools"] == [
            {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
        ]

    @pytest.mark.parametrize("nozzle", [0, "", "unknown", None])
    def test_omits_the_tool_when_no_usable_nozzle_is_reported(
        self, nozzle: object
    ) -> None:
        status = make_client()._normalize_status({"print": {"nozzle_diameter": nozzle}})

        assert "material_tools" not in status

    def test_exposes_every_material_source_as_a_slot(self) -> None:
        status = make_client()._normalize_status(
            {
                "print": {
                    **AMS_REPORT,
                    "vt_tray": {"tray_type": "PETG", "tray_color": "00FF00FF"},
                }
            }
        )

        assert status["material_slots"] == [
            {
                "slot_key": "ams:0:0",
                "label": "AMS 0 tray 0",
                "tool_key": "tool0",
                "state": "loaded",
                "material_type": "PLA",
                "color_hex": "#FF0000",
            },
            {
                "slot_key": "ams:0:1",
                "label": "AMS 0 tray 1",
                "tool_key": "tool0",
                "state": "empty",
                "material_type": None,
                "color_hex": None,
            },
            {
                "slot_key": "external",
                "label": "External spool",
                "tool_key": "tool0",
                "state": "loaded",
                "material_type": "PETG",
                "color_hex": "#00FF00",
            },
        ]

    def test_keeps_slots_an_incremental_report_did_not_mention(self) -> None:
        client = make_client()
        client._normalize_status({"print": AMS_REPORT})

        status = client._normalize_status(
            {
                "print": {
                    "ams": {
                        "ams": [
                            {
                                "id": "0",
                                "tray": [
                                    {
                                        "id": "1",
                                        "tray_type": "PETG",
                                        "tray_color": "00FF00FF",
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        )

        # Bambu sends deltas after the first full table. Replacing rather than
        # merging would blank every spool except the one that just changed.
        assert slots_by_key(status)["ams:0:0"]["material_type"] == "PLA"
        assert slots_by_key(status)["ams:0:1"]["material_type"] == "PETG"

    def test_records_a_tray_becoming_empty(self) -> None:
        client = make_client()
        client._normalize_status({"print": AMS_REPORT})

        status = client._normalize_status(
            {
                "print": {
                    "ams": {
                        "ams": [
                            {
                                "id": "0",
                                "tray": [
                                    {"id": "0", "tray_type": "", "tray_color": ""}
                                ],
                            }
                        ]
                    }
                }
            }
        )

        assert slots_by_key(status)["ams:0:0"]["state"] == "empty"

    def test_keeps_the_last_known_slots_when_a_report_is_malformed(self) -> None:
        client = make_client()
        good = client._normalize_status({"print": AMS_REPORT})

        malformed = client._normalize_status({"print": {"ams": {"ams": [None, "bad"]}}})

        # A garbled AMS frame must not empty the filament panel.
        assert malformed["material_slots"] == good["material_slots"]

    def test_omits_slots_entirely_before_any_ams_report_arrives(self) -> None:
        status = make_client()._normalize_status({"print": {"gcode_state": "IDLE"}})

        assert "material_slots" not in status


class TestNormalizeAmsSlots:
    def test_returns_nothing_when_the_report_has_no_ams_section(self) -> None:
        assert make_client()._normalize_ams_slots({}) == []

    def test_ignores_an_ams_section_that_is_not_a_mapping(self) -> None:
        assert make_client()._normalize_ams_slots({"ams": "unavailable"}) == []

    def test_ignores_a_unit_whose_trays_are_not_a_list(self) -> None:
        slots = make_client()._normalize_ams_slots(
            {"ams": {"ams": [{"id": "0", "tray": "broken"}]}}
        )

        assert slots == []

    def test_ignores_a_tray_entry_that_is_not_a_mapping(self) -> None:
        slots = make_client()._normalize_ams_slots(
            {"ams": {"ams": [{"id": "0", "tray": ["broken"]}]}}
        )

        assert slots == []

    def test_numbers_a_unit_that_reports_no_id(self) -> None:
        slots = make_client()._normalize_ams_slots(
            {"ams": {"ams": [{"tray": [{"tray_type": "PLA"}]}]}}
        )

        # The slot key is the identity the UI and the ledger join on, so it has
        # to exist even for a unit that reports none.
        assert slots[0]["slot_key"] == "ams:0:0"

    def test_drops_a_colour_too_short_to_be_a_hex_triplet(self) -> None:
        slots = make_client()._normalize_ams_slots(
            {"vt_tray": {"tray_type": "PLA", "tray_color": "FFF"}}
        )

        assert slots[0]["color_hex"] is None

    def test_drops_the_alpha_channel_from_a_tray_colour(self) -> None:
        slots = make_client()._normalize_ams_slots(
            {"vt_tray": {"tray_type": "PLA", "tray_color": "ff0000ff"}}
        )

        # Bambu sends RGBA; the UI's colour tokens are RGB.
        assert slots[0]["color_hex"] == "#FF0000"

    def test_reports_an_empty_external_spool(self) -> None:
        slots = make_client()._normalize_ams_slots({"vt_tray": {}})

        assert slots == [
            {
                "slot_key": "external",
                "label": "External spool",
                "tool_key": "tool0",
                "state": "empty",
                "material_type": None,
                "color_hex": None,
            }
        ]

    def test_ignores_a_virtual_tray_that_is_not_a_mapping(self) -> None:
        assert make_client()._normalize_ams_slots({"vt_tray": "none"}) == []


class TestNormalizeProjectRequest:
    def test_captures_the_artifact_url_alongside_the_status(self) -> None:
        status = make_client()._normalize_project_request(
            {
                "print": {
                    "command": "project_file",
                    "url": f"ftps://{SERIAL}/cache/benchy.3mf",
                    "gcode_state": "RUNNING",
                    "subtask_name": "Benchy",
                    "task_id": "task-42",
                }
            }
        )

        # This URL is the only way PrintStash learns where the bytes of a print
        # started from Bambu Studio live.
        assert status["print_stats"] == {
            "state": "printing",
            "filename": "Benchy",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_artifact_path": f"ftps://{SERIAL}/cache/benchy.3mf",
        }

    @pytest.mark.parametrize("field", ["file", "gcode_file"])
    def test_accepts_the_other_field_names_bambu_uses_for_the_path(
        self, field: str
    ) -> None:
        status = make_client()._normalize_project_request(
            {"print": {"command": "project_file", field: "cache/benchy.3mf"}}
        )

        assert status["print_stats"]["external_artifact_path"] == "cache/benchy.3mf"

    def test_ignores_a_report_that_is_not_a_project_file_command(self) -> None:
        assert (
            make_client()._normalize_project_request(
                {"print": {"command": "pushall", "url": "cache/benchy.3mf"}}
            )
            == {}
        )

    def test_ignores_a_report_with_no_print_section(self) -> None:
        assert make_client()._normalize_project_request({"print": "broken"}) == {}

    def test_returns_the_plain_status_when_the_command_carries_no_path(self) -> None:
        status = make_client()._normalize_project_request(
            {"print": {"command": "project_file", "gcode_state": "RUNNING"}}
        )

        assert status == {"print_stats": {"state": "printing"}}
