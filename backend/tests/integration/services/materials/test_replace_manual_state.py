"""An operator declaring what they just loaded, replacing what they said before.

This is a whole-state write, not a patch, and that is the right shape: an
operator looking at a machine knows what is in every slot, and a merge would
leave a slot they removed sitting in the state forever. It is also why the
optimistic-concurrency check matters — two operators at two machines, or one
with a stale browser tab, would otherwise silently overwrite each other's answer
about a physical fact.

The three validation refusals all guard against a state that reads as a fact but
is not one. Two slots with the same key collapse into one and the second write
wins silently. A slot pointing at a tool that is not declared belongs to no
extruder, so nothing routes off it. And a slot marked `loaded` with no material
is the worst of the three: it claims something *is* in there without saying what,
which is indistinguishable from a proven match to any check that only asks "is
this slot loaded?".
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import MaterialSource, Printer, User
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from app.services import materials
from tests.integration.services.materials.conftest import load_manual_state


class TestReplaceManualState:
    def test_records_the_state_as_operator_declared(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        state = materials.replace_manual_state(
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
                        slot_key="slot0",
                        label="Main spool",
                        tool_key="tool0",
                        state="loaded",
                        material_type="ABS",
                        color_hex="#000000",
                    )
                ],
            ),
            operator,
        )

        # The source is what decides whether a provider reading may shadow this
        # answer later, so it is part of the write rather than a display field.
        assert state.slots[0].source == MaterialSource.MANUAL

    def test_refuses_two_slots_with_the_same_key(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        payload = ManualMaterialStateUpdate(
            tools=[MaterialToolWrite(tool_key="tool0", label="One")],
            slots=[
                MaterialSlotWrite(slot_key="same", label="One"),
                MaterialSlotWrite(slot_key="same", label="Two"),
            ],
        )

        with pytest.raises(
            materials.MaterialStateError, match="material_slot_duplicate"
        ):
            materials.replace_manual_state(
                db_session, int(printer.id), payload, operator
            )

    def test_refuses_a_slot_pointing_at_a_tool_that_is_not_declared(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        payload = ManualMaterialStateUpdate(
            slots=[MaterialSlotWrite(slot_key="feed", label="Feed", tool_key="missing")]
        )

        # A slot on no extruder cannot be routed off, so it is a state the
        # feature has no meaning for.
        with pytest.raises(
            materials.MaterialStateError, match="material_slot_tool_unknown"
        ):
            materials.replace_manual_state(
                db_session, int(printer.id), payload, operator
            )

    def test_refuses_a_loaded_slot_that_does_not_say_what_is_loaded(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        payload = ManualMaterialStateUpdate(
            slots=[MaterialSlotWrite(slot_key="feed", label="Feed", state="loaded")]
        )

        # "Something is in here" without "what" is the one input that could read
        # as a proven match to a check that only asks whether the slot is loaded.
        with pytest.raises(
            materials.MaterialStateError, match="loaded_material_type_required"
        ):
            materials.replace_manual_state(
                db_session, int(printer.id), payload, operator
            )

    def test_refuses_a_write_based_on_a_stale_read(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        stale_version = printer.updated_at
        load_manual_state(db_session, printer, operator)

        with pytest.raises(
            materials.MaterialStateError, match="material_state_changed"
        ):
            materials.replace_manual_state(
                db_session,
                int(printer.id),
                ManualMaterialStateUpdate(expected_updated_at=stale_version),
                operator,
            )

    def test_reports_a_printer_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
            materials.replace_manual_state(
                db_session, 999_999, ManualMaterialStateUpdate(), operator
            )
