"""Reporting what is loaded in a printer, and how much to trust it.

Two sources describe the same slots. An operator can say what they loaded, and
some providers report it themselves (Bambu's AMS, Moonraker via Spoolman). They
disagree, and the resolution is not symmetric: a provider reading beats a manual
one, because the machine can see the spool and the operator is remembering.

But a provider reading is only worth more while it is *current*. A printer that
went offline is still carrying the last thing it reported, and somebody may have
changed the spool since — so a reading older than the printer's own last update
is marked stale, and every compatibility check downstream reads stale as
`unknown` rather than as fact. That single flag is what stops an offline printer
from confidently refusing or accepting a job on a spool nobody has seen.

`confidence` is the same judgement exposed to the UI, so an operator can see
whether the answer came from the machine or from a human.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import (
    MaterialSource,
    Printer,
    PrinterStatus,
    User,
)
from app.services import materials
from tests.factories import build_material_slot, build_printer, build_printer_tool
from tests.integration.services.materials.conftest import load_manual_state


class TestReadMaterialState:
    def test_reports_what_the_operator_recorded(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")

        state = materials.read_material_state(db_session, int(printer.id))

        assert [slot.material_type for slot in state.slots] == ["ABS"]
        assert state.slots[0].source == MaterialSource.MANUAL

    def test_prefers_a_reading_the_provider_made_itself(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")
        build_printer_tool(
            db_session, printer, label="Provider tool", source=MaterialSource.BAMBU_AMS
        )
        build_material_slot(
            db_session,
            printer,
            slot_key="slot0",
            label="Provider feed",
            source=MaterialSource.BAMBU_AMS,
            material_type="PLA",
        )

        state = materials.read_material_state(db_session, int(printer.id))

        # The machine can see the spool; the operator is remembering. Shadowing
        # rather than merging matters because a merged view would show two slots
        # for one physical position.
        assert [row.label for row in state.tools] == ["Provider tool"]
        assert [row.label for row in state.slots] == ["Provider feed"]

    def test_marks_a_provider_reading_as_provider_reported(
        self, db_session: Session, printer: Printer
    ) -> None:
        build_printer_tool(db_session, printer, source=MaterialSource.BAMBU_AMS)
        build_material_slot(db_session, printer, source=MaterialSource.BAMBU_AMS)

        state = materials.read_material_state(db_session, int(printer.id))

        assert state.slots[0].confidence == "provider_reported"

    def test_marks_a_provider_reading_stale_while_the_printer_is_offline(
        self, db_session: Session
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

        state = materials.read_material_state(db_session, int(offline.id))

        # Somebody may have changed the spool while the printer was unreachable.
        # Downstream, stale reads as `unknown` rather than as fact.
        assert state.slots[0].stale is True

    def test_trusts_the_reading_again_once_the_printer_reconnects(
        self, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session,
            name="Telemetry",
            moonraker_url="http://telemetry",
            status=PrinterStatus.OFFLINE,
        )
        build_printer_tool(
            db_session, printer, source=MaterialSource.MOONRAKER_SPOOLMAN
        )
        build_material_slot(
            db_session, printer, source=MaterialSource.MOONRAKER_SPOOLMAN
        )

        printer.status = PrinterStatus.READY
        db_session.add(printer)
        db_session.commit()

        state = materials.read_material_state(db_session, int(printer.id))
        assert state.slots[0].stale is False

    def test_reports_a_printer_that_does_not_exist(self, db_session: Session) -> None:
        with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
            materials.read_material_state(db_session, 999_999)
