"""Deciding which spool a job will actually consume, or declining to guess.

When a print starts, PrintStash records the spool it drew from so filament
tracking has something to decrement. It can only do that if the answer is
unambiguous: exactly one loaded slot holds the material the job needs. Two slots
with the same filament is a real and common setup — an AMS with backup spools —
and picking one of them would silently decrement the wrong inventory, which is
worse than not tracking at all because it looks like it worked.

So this returns nothing rather than a guess in every unclear case, and the print
proceeds untracked. The unsaved-printer case is the same rule reached from
another direction: a printer with no id has no slots to read, and must produce
"no answer" instead of raising out of the routing path.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import File, MaterialSource, Printer, PrinterStatus
from app.services import fleet
from tests.factories import build_material_slot, build_printer, printer_config
from tests.integration.services.fleet.conftest import snapshot_for


def _load(session: Session, printer: Printer, slot_key: str, material, spool_id: int):
    return build_material_slot(
        session,
        printer,
        slot_key=slot_key,
        material_type=material,
        spool_id=spool_id,
        spool_name=f"Spool {spool_id}",
        spool_filament_id=spool_id * 10,
    )


class TestUnambiguousTrackingSpool:
    def test_resolves_the_one_slot_holding_the_required_filament(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        _load(db_session, printer, "matching", "PLA", 7)
        _load(db_session, printer, "wrong", "ABS", 8)
        _load(db_session, printer, "unknown", None, 9)

        resolved = fleet._unambiguous_tracking_spool(
            printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # The id, the name and the filament id together: tracking needs the
        # filament id to decrement inventory and the name to show what it used.
        assert resolved == (7, "Spool 7", 70)

    def test_declines_to_guess_when_two_slots_hold_the_required_filament(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        _load(db_session, printer, "matching", "PLA", 7)
        _load(db_session, printer, "backup", "PLA", 8)

        resolved = fleet._unambiguous_tracking_spool(
            printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # An AMS with a backup spool is an ordinary setup. Decrementing the wrong
        # one is worse than not tracking, because it looks like it worked.
        assert resolved == (None, None, None)

    def test_reports_no_spool_for_a_printer_that_has_no_id_yet(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        resolved = fleet._unambiguous_tracking_spool(
            printer_config("Unsaved"),
            int(pla_artifact.id),
            snapshot_for(db_session, pla_artifact),
        )

        # Reached from printer creation, before the row has an id: must answer
        # "no spool" rather than raise out of the routing path.
        assert resolved == (None, None, None)

    def test_ignores_a_provider_slot_the_printer_is_no_longer_reporting(
        self, db_session: Session, pla_artifact: File
    ) -> None:
        offline = build_printer(
            db_session,
            name="Offline tracker",
            moonraker_url="http://offline-tracker",
            status=PrinterStatus.OFFLINE,
        )
        build_material_slot(
            db_session,
            offline,
            slot_key="tracked",
            material_type="PLA",
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            spool_id=7,
            spool_name="Spool 7",
            spool_filament_id=70,
        )

        resolved = fleet._unambiguous_tracking_spool(
            offline, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # The last thing an unreachable printer said about its spool is not
        # evidence of what is loaded now, so decrementing it would consume
        # inventory the machine may not have touched.
        assert resolved == (None, None, None)

    def test_ignores_an_operators_answer_the_provider_has_superseded(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        build_material_slot(
            db_session,
            printer,
            slot_key="feed",
            material_type="PLA",
            source=MaterialSource.MANUAL,
            spool_id=7,
            spool_name="Manual spool",
            spool_filament_id=70,
        )
        build_material_slot(
            db_session,
            printer,
            slot_key="feed",
            material_type="PLA",
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            spool_id=8,
            spool_name="Reported spool",
            spool_filament_id=80,
        )

        resolved = fleet._unambiguous_tracking_spool(
            printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # Same physical slot, two answers. Counting both would look like an
        # ambiguity and stop tracking, so the shadowed manual row drops out and
        # the machine's own reading is the one consumed.
        assert resolved == (8, "Reported spool", 80)

    def test_ignores_a_slot_that_is_not_loaded(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        _load(db_session, printer, "matching", "PLA", 7)
        build_material_slot(
            db_session,
            printer,
            slot_key="empty",
            material_type="PLA",
            state="empty",
            spool_id=8,
        )

        resolved = fleet._unambiguous_tracking_spool(
            printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # An empty slot still remembers the last spool that sat in it. Treating
        # that as a second candidate would silently stop tracking a printer that
        # has exactly one usable spool.
        assert resolved == (7, "Spool 7", 70)

    def test_ignores_a_loaded_slot_with_no_spool_to_charge(
        self, db_session: Session, printer: Printer, pla_artifact: File
    ) -> None:
        _load(db_session, printer, "matching", "PLA", 7)
        build_material_slot(
            db_session, printer, slot_key="untracked", material_type="PLA"
        )

        resolved = fleet._unambiguous_tracking_spool(
            printer, int(pla_artifact.id), snapshot_for(db_session, pla_artifact)
        )

        # Loaded but not tracked: there is no inventory row to decrement, so it
        # is not a competing candidate.
        assert resolved == (7, "Spool 7", 70)
