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
from app.modules.printing import materials
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from tests.factories import (
    a_gcode_artifact,
    build_material_requirement,
    build_material_slot,
    build_metadata,
    build_printer,
    build_printer_tool,
)
from tests.integration.modules.printing.materials.conftest import (
    load_manual_state,
    requiring,
)


def declare_feeds(
    session: Session,
    printer: Printer,
    operator: User,
    types: list[str],
    *,
    tool_keys: list[str] | None = None,
    colors: list[str | None] | None = None,
) -> None:
    """Record loaded feeds, one per material, optionally on distinct tools."""
    keys = tool_keys or ["tool0"] * len(types)
    materials.replace_manual_state(
        session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[
                MaterialToolWrite(tool_key=key, label=key, nozzle_diameter_mm=0.4)
                for key in dict.fromkeys(keys)
            ],
            slots=[
                MaterialSlotWrite(
                    slot_key=f"feed-{index}",
                    label=f"Feed {index}",
                    tool_key=key,
                    state="loaded",
                    material_type=material,
                    color_hex=colors[index] if colors else None,
                )
                for index, (material, key) in enumerate(zip(types, keys, strict=True))
            ],
        ),
        operator,
    )


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

    def test_places_a_multi_material_job_on_a_shared_tool_by_material(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PETG"
        )
        declare_feeds(db_session, printer, operator, ["PLA", "PETG"])

        # An AMS reaches one extruder through every tray, so the job needs a
        # distinct tray per material and nothing more. It used to be `unknown`
        # for every multi-material file on every such printer.
        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.verdict == "compatible"

    def test_a_single_used_slot_beyond_the_first_needs_one_feed(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        # Issue #445: a plate printed from slicer slot 3 asks for one material,
        # recorded at tool index 2. A four-tray AMS holding it somewhere fits.
        artifact = a_gcode_artifact(db_session, "slot-3")
        build_metadata(
            db_session, artifact, material_type="PLA", nozzle_diameter_mm=0.4
        )
        build_material_requirement(
            db_session, artifact, tool_index=2, material_type="PLA"
        )
        declare_feeds(db_session, printer, operator, ["PETG", "ABS", "PLA", "PETG"])

        assert (
            materials.compatibility_for_printer(
                db_session, int(artifact.id), int(printer.id)
            ).verdict
            == "compatible"
        )

    def test_two_requirements_for_one_material_need_two_feeds(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PLA"
        )
        declare_feeds(db_session, printer, operator, ["PLA", "PETG"])

        # One PLA spool cannot print a white-and-black PLA job. The set of
        # required *types* was {PLA}, which one spool satisfied.
        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.verdict == "mismatch"
        assert result.missing_materials == ("pla",)

    def test_two_requirements_for_one_material_fit_two_feeds(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PLA"
        )
        declare_feeds(db_session, printer, operator, ["PLA", "PETG", "PLA"])

        assert (
            materials.compatibility_for_printer(
                db_session, int(artifact.id), int(printer.id)
            ).verdict
            == "compatible"
        )

    def test_a_multi_tool_job_names_a_tool_the_printer_does_not_report(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=2, material_type="PETG"
        )
        declare_feeds(
            db_session, printer, operator, ["PLA", "PETG"], tool_keys=["tool0", "tool1"]
        )

        # Two independent tools were reported, and the job asks for a third.
        # Which feed would supply it is a guess.
        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.verdict == "unknown"
        assert "tool_feed_mapping_unknown" in result.reasons

    def test_a_toolchanger_feed_of_the_wrong_material_is_a_mismatch(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PETG"
        )
        declare_feeds(
            db_session, printer, operator, ["PLA", "ABS"], tool_keys=["tool0", "tool1"]
        )

        # PETG is not loaded anywhere, and tool 1 holds ABS: a proven mismatch.
        assert (
            materials.compatibility_for_printer(
                db_session, int(artifact.id), int(printer.id)
            ).verdict
            == "mismatch"
        )

    def test_places_each_requirement_on_the_feed_of_its_own_colour(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session,
            artifact,
            tool_index=1,
            material_type="PLA",
            color_hex="#00FF00",
        )
        declare_feeds(
            db_session,
            printer,
            operator,
            ["PLA", "PLA"],
            colors=["#00FF00", "#FF0000"],
        )

        # The trays are loaded in the opposite order to the plate's slots, yet
        # each colour has a tray of its own, so nothing is worth advising.
        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.verdict == "compatible"
        assert result.color_advisories == ()

    def test_advises_each_requirement_against_the_feed_it_is_placed_on(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session,
            artifact,
            tool_index=1,
            material_type="PLA",
            color_hex="#00FF00",
        )
        declare_feeds(
            db_session,
            printer,
            operator,
            ["PLA", "PLA"],
            colors=["#111111", "#222222"],
        )

        result = materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        )

        assert result.verdict == "compatible"
        assert result.color_advisories == (
            "tool0:#FF0000->#111111",
            "tool1:#00FF00->#222222",
        )

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
