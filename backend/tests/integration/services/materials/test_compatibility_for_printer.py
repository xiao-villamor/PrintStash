"""Deciding whether one printer can print one artifact, in three answers.

This is the function the whole feature rests on, and its contract is that
`unknown` is a real answer rather than a soft no. A printer that cannot report
its material, is offline, has a tracked spool nobody resolved, or has more
required extruders than mapped slots is *unproven* — and calling that a mismatch
would refuse to print on most of a real fleet, while calling it compatible
defeats the point. So there are exactly three verdicts, and routing treats them
in that order.

`mismatch` is reserved for the cases the software can actually prove: the loaded
material is not the required one, or the nozzle is the wrong diameter. Both waste
a spool or damage a nozzle, and both are worth blocking on.

Colour is deliberately not one of them. Printing in the wrong colour is a
preference, so it comes back as an advisory that routing ignores — a blocked job
over a colour would be a false positive nobody wants. And material comparison is
case- and whitespace-insensitive, because " pla " out of a slicer comment and
"PLA" off a spool label are the same filament.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import File, MaterialSource, Printer, PrinterStatus, User
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from app.services import materials
from tests.factories import (
    a_gcode_artifact,
    build_material_requirement,
    build_material_slot,
    build_printer,
    build_printer_tool,
)
from tests.integration.services.materials.conftest import load_manual_state, requiring


class TestCompatibilityForPrinter:
    def test_reports_a_fully_matching_printer_as_compatible(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        load_manual_state(db_session, printer, operator, material="PLA")

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(printer.id)
        )

        assert result.verdict == "compatible"

    @pytest.mark.parametrize("loaded", [" pla ", "PLA", "Pla", "pla"])
    def test_matches_a_material_however_it_is_spelled(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
        loaded: str,
    ) -> None:
        load_manual_state(db_session, printer, operator, material=loaded)

        # " pla " comes out of a slicer comment and "PLA" off a spool label.
        # They are the same filament, and treating them as different would make
        # the feature refuse correct jobs.
        assert (
            materials.compatibility_for_printer(
                db_session, int(pla_artifact.id), int(printer.id)
            ).verdict
            == "compatible"
        )

    def test_reports_a_colour_difference_as_advisory_rather_than_a_mismatch(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        load_manual_state(
            db_session, printer, operator, material="PLA", color="#0000FF"
        )

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(printer.id)
        )

        # Printing in the wrong colour is a preference, not a failure. Blocking
        # on it would be a false positive on every fleet with mixed spools.
        assert result.verdict == "compatible"
        assert result.color_advisories

    def test_reports_the_wrong_material_as_a_mismatch(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(printer.id)
        )

        assert result.verdict == "mismatch"
        assert result.reasons == ("material_type_mismatch",)

    def test_reports_the_wrong_nozzle_as_a_mismatch(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        load_manual_state(db_session, printer, operator, material="PLA", nozzle=0.6)

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(printer.id)
        )

        # The right filament through the wrong nozzle is still a failed print,
        # and a 0.4 job on a 0.6 nozzle is the common way a fleet gets it wrong.
        assert result.verdict == "mismatch"
        assert result.reasons == ("nozzle_diameter_mismatch",)

    def test_reports_an_artifact_with_no_requirements_as_unknown(
        self, db_session: Session, printer: Printer
    ) -> None:
        artifact = a_gcode_artifact(db_session, "No requirements")

        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.reasons == ("job_material_unknown",)

    def test_reports_an_offline_printers_last_reading_as_unknown(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        offline = build_printer(
            db_session,
            name="Telemetry",
            moonraker_url="http://telemetry",
            status=PrinterStatus.OFFLINE,
        )
        build_printer_tool(
            db_session, offline, source=MaterialSource.MOONRAKER_SPOOLMAN
        )
        build_material_slot(
            db_session, offline, source=MaterialSource.MOONRAKER_SPOOLMAN
        )

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(offline.id)
        )

        # The spool may have changed while the printer was unreachable, so the
        # stale reading is unproven rather than wrong.
        assert result.verdict == "unknown"

    def test_reports_a_tracked_spool_it_cannot_resolve_as_unknown(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        build_printer_tool(
            db_session, printer, source=MaterialSource.MOONRAKER_SPOOLMAN
        )
        build_material_slot(
            db_session,
            printer,
            label="Moonraker active spool",
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            material_type=None,
            spool_id=42,
        )

        result = materials.compatibility_for_printer(
            db_session, int(pla_artifact.id), int(printer.id)
        )

        # Moonraker names a spool id but not what is on it. That is the single
        # most common real state, and calling it a mismatch would block most
        # Moonraker fleets outright.
        assert result.verdict == "unknown"
        assert result.missing_materials == ("pla",)

    def test_reports_a_multi_tool_artifact_it_cannot_map_as_unknown(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PETG"
        )
        materials.replace_manual_state(
            db_session,
            int(printer.id),
            ManualMaterialStateUpdate(
                tools=[MaterialToolWrite(tool_key="tool0", label="Tool 0")],
                slots=[
                    MaterialSlotWrite(
                        slot_key="feed",
                        label="Feed",
                        tool_key="tool0",
                        state="loaded",
                        material_type="PLA",
                    ),
                    MaterialSlotWrite(
                        slot_key="feed-2",
                        label="Feed 2",
                        tool_key="tool0",
                        state="loaded",
                        material_type="PETG",
                    ),
                ],
            ),
            operator,
        )

        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        # Both materials are loaded, but on one extruder — so which slot feeds
        # tool 1 is a guess, and guessing wrong prints the job in two swapped
        # colours or materials.
        assert result.verdict == "unknown"
        assert "tool_feed_mapping_unknown" in result.reasons

    def test_reports_a_printer_that_never_declared_a_nozzle_as_unknown(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        materials.replace_manual_state(
            db_session,
            int(printer.id),
            ManualMaterialStateUpdate(
                tools=[MaterialToolWrite(tool_key="tool0", label="Tool 0")],
                slots=[
                    MaterialSlotWrite(
                        slot_key="feed",
                        label="Feed",
                        tool_key="tool0",
                        state="loaded",
                        material_type="PLA",
                    )
                ],
            ),
            operator,
        )
        artifact = requiring(db_session, material="PLA", nozzle=0.4)

        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert "printer_nozzle_unknown" in result.reasons

    def test_reports_an_artifact_that_does_not_exist(
        self, db_session: Session, printer: Printer
    ) -> None:
        with pytest.raises(materials.MaterialStateError, match="file_not_found"):
            materials.compatibility_for_printer(db_session, 999_999, int(printer.id))

    def test_reports_a_printer_that_does_not_exist(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
            materials.compatibility_for_printer(
                db_session, int(pla_artifact.id), 999_999
            )
