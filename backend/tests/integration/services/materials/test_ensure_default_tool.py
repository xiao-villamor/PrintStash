"""Giving a printer one extruder to hang its filament state off.

Almost every printer has exactly one extruder, and the material state is
modelled as tools with slots attached — so without a default tool the common
case would need an operator to declare "tool0" before they could say what is
loaded. This function fills that in.

The case worth a test is the unsaved printer. `ensure_default_tool` is called
during printer creation, before the row has an id, so it has to tolerate that
rather than inserting a tool row pointing at `None` — a row that belongs to no
printer and shows up in every unfiltered read.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import PrinterTool
from app.services import materials
from tests.factories import printer_config


class TestEnsureDefaultTool:
    def test_adds_no_tool_for_a_printer_that_has_no_id_yet(
        self, db_session: Session
    ) -> None:
        materials.ensure_default_tool(db_session, printer_config("Unsaved"))

        # A tool row whose `printer_id` is null belongs to no printer and is
        # invisible to every scoped read, so it would never be cleaned up.
        assert db_session.exec(select(PrinterTool)).all() == []
