"""Picking the machine a queued job should go to.

The routing preference is the whole material-aware feature expressed as an
ordering: a printer proven to have the right filament wins, a printer whose state
is *unproven* is next, and a printer proven to have the wrong filament is not
picked at all. That middle tier is what makes the feature shippable — most real
printers cannot report their spool, and a router that treated unproven as
unusable would refuse to print on almost anything.

Preferring a proven match over an unproven one is not just optimisation: it means
adding material state to one printer makes routing better rather than changing
nothing, so an operator who does the work sees a result.

Blocking is reported as a reason rather than an exception because the job stays
queued and the reason is shown against it — an operator can load the right
filament, or override with `ALLOW_MISMATCH` and accept the risk. Drain mode is
folded into the same decision, so a machine somebody is standing at drops out of
routing without being deleted.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import (
    CompatibilityPolicy,
    File,
    Printer,
    PrinterStatus,
    RoutingStrategy,
    User,
)
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from app.services import fleet, materials
from tests.factories import build_material_requirement, build_printer, printer_config
from tests.integration.services.fleet.conftest import (
    drain,
    load_manual_state,
    requiring,
    snapshot_for,
)


@pytest.fixture
def fleet_of_three(
    db_session: Session, operator: User, pla_artifact: File
) -> dict[str, Printer]:
    """One printer proven right, one unproven, one proven wrong.

    All three tiers in one fixture because the ordering is only meaningful
    between them: a test with two of the three cannot tell "prefers compatible"
    from "avoids mismatch".
    """
    printers = {
        name: build_printer(
            db_session,
            name=name,
            moonraker_url=f"http://{name.lower()}",
            status=PrinterStatus.READY,
        )
        for name in ("PLA", "Unknown", "ABS")
    }
    load_manual_state(db_session, printers["PLA"], operator, material="PLA")
    load_manual_state(db_session, printers["ABS"], operator, material="ABS")
    return printers


class TestChoosePrinter:
    def test_prefers_a_printer_proven_to_have_the_right_filament(
        self,
        db_session: Session,
        pla_artifact: File,
        fleet_of_three: dict[str, Printer],
    ) -> None:
        selected, blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, pla_artifact),
            file_id=int(pla_artifact.id),
        )

        assert selected is not None and selected.id == fleet_of_three["PLA"].id
        assert blocked is None

    def test_falls_back_to_a_printer_whose_state_is_unproven(
        self,
        db_session: Session,
        pla_artifact: File,
        fleet_of_three: dict[str, Printer],
    ) -> None:
        drain(db_session, fleet_of_three["PLA"])

        selected, blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, pla_artifact),
            file_id=int(pla_artifact.id),
        )

        # Most real printers cannot report their spool. Skipping them would leave
        # the feature refusing to print on nearly the whole fleet.
        assert selected is not None and selected.id == fleet_of_three["Unknown"].id
        assert blocked is None

    def test_picks_nothing_when_only_a_proven_mismatch_is_left(
        self,
        db_session: Session,
        pla_artifact: File,
        fleet_of_three: dict[str, Printer],
    ) -> None:
        drain(db_session, fleet_of_three["PLA"])
        drain(db_session, fleet_of_three["Unknown"])

        selected, blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, pla_artifact),
            file_id=int(pla_artifact.id),
        )

        # The job stays queued with a reason rather than starting a print that
        # wastes a spool. An operator loads the right filament, or overrides.
        assert selected is None
        assert blocked == "no_material_compatible_printer"

    def test_picks_a_proven_mismatch_when_the_caller_allows_one(
        self,
        db_session: Session,
        pla_artifact: File,
        fleet_of_three: dict[str, Printer],
    ) -> None:
        drain(db_session, fleet_of_three["PLA"])
        drain(db_session, fleet_of_three["Unknown"])

        selected, blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, pla_artifact),
            file_id=int(pla_artifact.id),
            compatibility_policy=CompatibilityPolicy.ALLOW_MISMATCH,
        )

        # The operator has seen the warning and accepted it; the router must not
        # keep refusing on their behalf.
        assert selected is not None and selected.id == fleet_of_three["ABS"].id
        assert blocked is None

    def test_blocks_the_default_strategy_when_the_default_printer_cannot_print_it(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")
        printer.is_default = True
        db_session.add(printer)
        db_session.commit()

        _selected, blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.DEFAULT,
            None,
            snapshot=snapshot_for(db_session, pla_artifact),
            file_id=int(pla_artifact.id),
        )

        # "Default" does not mean "regardless": a default printer loaded with the
        # wrong filament blocks like any other proven mismatch.
        assert blocked == "no_material_compatible_printer"

    def test_reports_a_manual_target_that_does_not_exist(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        with pytest.raises(fleet.FleetError, match="printer_not_found"):
            fleet.choose_printer(
                db_session,
                RoutingStrategy.MANUAL,
                999_999,
                snapshot=snapshot_for(db_session, pla_artifact),
                file_id=int(pla_artifact.id),
            )

    def test_only_considers_printers_in_the_requested_group(
        self, db_session: Session, artifact: File
    ) -> None:
        build_printer(
            db_session,
            name="Room A",
            moonraker_url="http://room-a",
            status=PrinterStatus.READY,
            group="room-a",
        )
        elsewhere = build_printer(
            db_session,
            name="Room B",
            moonraker_url="http://room-b",
            status=PrinterStatus.READY,
            group="room-b",
        )

        selected, _blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, artifact),
            file_id=int(artifact.id),
            target_group="room-a",
        )

        # A group is a physical place. Routing a job out of the room the operator
        # named leaves a print waiting on a machine nobody is standing at.
        assert selected is not None and selected.id != elsewhere.id
        assert selected.group == "room-a"

    def test_picks_nothing_when_the_requested_group_is_empty(
        self, db_session: Session, artifact: File, printer: Printer
    ) -> None:
        selected, _blocked = fleet.choose_printer(
            db_session,
            RoutingStrategy.LEAST_BUSY,
            None,
            snapshot=snapshot_for(db_session, artifact),
            file_id=int(artifact.id),
            target_group="room-nobody-has",
        )

        # Falling back to the whole fleet would quietly ignore the constraint.
        assert selected is None


class TestCompatibilityRank:
    def test_ranks_an_unproven_printer_ahead_of_a_proven_mismatch(
        self,
        db_session: Session,
        printer: Printer,
        operator: User,
        pla_artifact: File,
    ) -> None:
        unproven = build_printer(
            db_session,
            name="Unproven",
            moonraker_url="http://unproven",
            status=PrinterStatus.READY,
        )
        load_manual_state(db_session, printer, operator, material="ABS")
        snapshot = snapshot_for(db_session, pla_artifact)

        # The rank is the ordering `choose_printer` sorts on, asserted directly
        # so a reordering shows up here rather than as one surprising selection.
        assert fleet._compatibility_rank(unproven, int(pla_artifact.id), snapshot) == 1
        assert fleet._compatibility_rank(printer, int(pla_artifact.id), snapshot) == 2

    def test_ranks_a_printer_with_no_id_as_unproven(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        # Reached while a printer is still being created. Ranking it as a
        # mismatch would exclude a machine nobody has described yet.
        assert (
            fleet._compatibility_rank(
                printer_config("Unsaved"),
                int(pla_artifact.id),
                snapshot_for(db_session, pla_artifact),
            )
            == 1
        )

    def test_ranks_a_printer_that_declared_no_nozzle_as_unproven(
        self, db_session: Session, printer: Printer, operator: User, pla_artifact: File
    ) -> None:
        load_manual_state(db_session, printer, operator, material="PLA", nozzle=None)

        assert (
            fleet._compatibility_rank(
                printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
            )
            == 1
        )

    def test_ranks_the_wrong_nozzle_as_a_mismatch(
        self, db_session: Session, printer: Printer, operator: User, pla_artifact: File
    ) -> None:
        load_manual_state(db_session, printer, operator, material="PLA", nozzle=0.6)

        assert (
            fleet._compatibility_rank(
                printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
            )
            == 2
        )

    def test_ranks_a_multi_tool_job_it_cannot_map_as_unproven(
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
                tools=[
                    MaterialToolWrite(
                        tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4
                    )
                ],
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

        # Both required materials are loaded, so nothing is a proven mismatch —
        # but they are on one extruder, so which slot feeds tool 1 is a guess.
        # Ranking it compatible would route a two-material print to a machine
        # that would print it in the wrong order.
        assert (
            fleet._compatibility_rank(
                printer, int(artifact.id), snapshot_for(db_session, artifact)
            )
            == 1
        )
