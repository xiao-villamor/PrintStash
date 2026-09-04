"""One artifact against several printers, for the operator's "where can I print this?"

`compatibility_for_printer` answers about one machine; this is what the UI calls
when someone is choosing. Two things make it more than a loop.

It lists the artifact's requirements per extruder, because a multi-tool job's
verdict is unreadable without them — "unknown" on a two-material print means
something different depending on which tool could not be matched, and the
operator fixing it needs to know which spool to change.

And it carries a verdict per printer rather than a single best answer, so the
list can be shown ranked and greyed rather than filtered down to one machine the
operator did not choose.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import Printer, User
from app.services import materials
from tests.factories import build_material_requirement
from tests.integration.services.materials.conftest import requiring


class TestCompatibilityReport:
    def test_lists_the_artifacts_requirement_for_every_extruder(
        self, db_session: Session, printer: Printer
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        build_material_requirement(
            db_session, artifact, tool_index=1, material_type="PETG"
        )

        report = materials.compatibility_report(
            db_session, int(artifact.id), [int(printer.id)]
        )

        # Without the per-tool breakdown an "unknown" verdict on a two-material
        # print does not tell the operator which spool to change.
        assert [row.tool_index for row in report.requirements] == [0, 1]

    def test_reports_a_verdict_for_each_printer_it_was_asked_about(
        self, db_session: Session, printer: Printer, operator: User
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)

        report = materials.compatibility_report(
            db_session, int(artifact.id), [int(printer.id)]
        )

        # A printer with nothing declared is unproven, not excluded: the operator
        # sees it greyed with a reason rather than missing from the list.
        assert [row.verdict for row in report.printers] == ["unknown"]
