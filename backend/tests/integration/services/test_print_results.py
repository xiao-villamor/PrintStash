"""What a finished print actually cost, and what it consumed.

A completed job records numbers people plan with, so where each one comes from matters
more than the arithmetic. The rule is **measured beats estimated**: a printer that reports
what it really used wins over the slicer's guess, and a spool's own linked filament
profile wins over a fuzzy material-name match, because a fuzzy match on "PLA" will happily
price a print with somebody else's spool.

The two side effects are both allowed to fail without taking the print with them. A
revision is promoted to `known_good` only from an unset or `needs_test` status — a human's
`failed` verdict is never overwritten by a lucky print. And the Spoolman write-back
refuses in five distinct situations, one of which is the subtle one: if Moonraker's own
Spoolman hook is already decrementing the active spool, writing again would double-count,
so PrintStash stands down unless explicitly told to force it.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import FilamentProfile, File, FileType, Metadata, PrintJob
from app.services import print_results, spoolman
from tests.factories import (
    build_file,
    build_model,
    build_print_job,
    print_job_config,
)


def _seed_file(db_session: Session, *, sha: str) -> File:
    m = build_model(db_session, name="M", slug=f"m-{sha}", hash=sha * 64)
    f = build_file(
        db_session,
        m,
        path=f"/data/{sha}.gcode",
        filename=f"{sha}.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=1,
        sha256=sha * 64,
    )
    return f


class TestResolveCompletionCost:
    def test_measured_values_win_over_slicer_estimate(self, db_session: Session):
        db_session.add(
            FilamentProfile(
                name="Hatchbox PLA",
                material_type="PLA",
                material_brand="Hatchbox",
                cost_per_kg=20.0,
            )
        )
        db_session.commit()
        f = _seed_file(db_session, sha="1")
        db_session.add(
            Metadata(file_id=f.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.commit()

        job = print_job_config(
            f, remote_filename="x", filament_used_g=50.0, actual_duration_s=600
        )
        grams, cost = print_results.resolve_completion_cost(db_session, job)
        assert (grams, cost) == (50.0, 1.0)

    def test_falls_back_to_slicer_estimate_grams(self, db_session: Session):
        db_session.add(
            FilamentProfile(
                name="Hatchbox PLA",
                material_type="PLA",
                material_brand="Hatchbox",
                cost_per_kg=20.0,
            )
        )
        db_session.commit()
        f = _seed_file(db_session, sha="2")
        db_session.add(
            Metadata(
                file_id=f.id,
                material_type="PLA",
                material_brand="Hatchbox",
                filament_weight_g=30.0,
            )
        )
        db_session.commit()

        job = print_job_config(f, remote_filename="x")
        grams, cost = print_results.resolve_completion_cost(db_session, job)
        assert (grams, cost) == (30.0, 0.6)

    def test_slicer_cost_used_when_no_profile_match(self, db_session: Session):
        f = _seed_file(db_session, sha="3")
        db_session.add(
            Metadata(
                file_id=f.id,
                material_type="ABS",
                filament_weight_g=10.0,
                filament_cost=3.5,
            )
        )
        db_session.commit()

        job = print_job_config(f, remote_filename="x")
        _, cost = print_results.resolve_completion_cost(db_session, job)
        assert cost == 3.5

    def test_spool_linked_profile_preferred_over_fuzzy_match(self, db_session: Session):
        db_session.add_all(
            [
                FilamentProfile(
                    name="Fuzzy PLA",
                    material_type="PLA",
                    material_brand="Hatchbox",
                    cost_per_kg=20.0,
                ),
                FilamentProfile(
                    name="Spool profile",
                    material_type="PLA",
                    material_brand="Other",
                    cost_per_kg=50.0,
                    spoolman_filament_id=7,
                ),
            ]
        )
        db_session.commit()
        f = _seed_file(db_session, sha="4")
        db_session.add(
            Metadata(file_id=f.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.commit()

        job = print_job_config(
            f, remote_filename="x", filament_used_g=100.0, spool_filament_id=7
        )
        grams, cost = print_results.resolve_completion_cost(db_session, job)
        # 100g @ 50/kg via the exact spool profile, not the fuzzy 20/kg match.
        assert (grams, cost) == (100.0, 5.0)


class TestLinkedProfileForSpool:
    def test_finds_the_profile_a_spool_was_synced_from(
        self, db_session: Session
    ) -> None:
        profile = FilamentProfile(
            name="PETG", material_type="PETG", cost_per_kg=20.0, spoolman_filament_id=7
        )
        db_session.add(profile)
        db_session.commit()

        found = print_results.linked_profile_for_spool(db_session, 7)

        assert found is not None
        assert found.id == profile.id

    def test_finds_nothing_for_a_spool_with_no_synced_profile(
        self, db_session: Session
    ) -> None:
        assert print_results.linked_profile_for_spool(db_session, 999) is None

    def test_asks_nothing_when_the_job_names_no_filament(
        self, db_session: Session
    ) -> None:
        assert print_results.linked_profile_for_spool(db_session, None) is None


class TestMarkKnownGoodIfEligible:
    def test_promotes_a_revision_that_has_never_been_judged(
        self, db_session: Session
    ) -> None:
        from app.db.models import FileRevisionStatus

        row = _seed_file(db_session, sha="a")

        assert print_results.mark_known_good_if_eligible(db_session, row.id) is True
        db_session.refresh(row)
        assert row.revision_status == FileRevisionStatus.KNOWN_GOOD

    def test_promotes_a_revision_that_was_awaiting_a_test(
        self, db_session: Session
    ) -> None:
        from app.db.models import FileRevisionStatus

        row = _seed_file(db_session, sha="b")
        row.revision_status = FileRevisionStatus.NEEDS_TEST
        db_session.add(row)
        db_session.commit()

        assert print_results.mark_known_good_if_eligible(db_session, row.id) is True

    def test_never_overwrites_a_humans_verdict(self, db_session: Session) -> None:
        from app.db.models import FileRevisionStatus

        row = _seed_file(db_session, sha="c")
        row.revision_status = FileRevisionStatus.FAILED
        db_session.add(row)
        db_session.commit()

        # One lucky print does not undo somebody marking this revision bad.
        assert print_results.mark_known_good_if_eligible(db_session, row.id) is False
        db_session.refresh(row)
        assert row.revision_status == FileRevisionStatus.FAILED

    def test_reports_a_revision_that_does_not_exist(self, db_session: Session) -> None:
        assert print_results.mark_known_good_if_eligible(db_session, 999_999) is False

    def test_reports_a_revision_that_is_in_the_trash(self, db_session: Session) -> None:
        from app.core.time import utcnow

        row = _seed_file(db_session, sha="d")
        row.deleted_at = utcnow()
        db_session.add(row)
        db_session.commit()

        assert print_results.mark_known_good_if_eligible(db_session, row.id) is False


class TestRecordSpoolUsage:
    def _job(self, db_session: Session, **overrides) -> PrintJob:
        row = _seed_file(db_session, sha=overrides.pop("sha", "e"))
        job = print_job_config(
            row, **{"spool_id": 5, "filament_used_g": 12.0, **overrides}
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job

    def _enable(self, db_session: Session, **flags: object) -> None:
        from app.services import runtime_config

        runtime_config.set_spoolman_enabled(db_session, True)
        runtime_config.set_spoolman_config(db_session, base_url="http://spoolman.local")
        for name, value in flags.items():
            getattr(runtime_config, f"set_{name}")(db_session, value)

    def test_declines_a_job_that_names_no_spool(self, db_session: Session) -> None:
        job = self._job(db_session, spool_id=None, sha="f")

        assert print_results.record_spool_usage(db_session, job) is False

    def test_declines_a_job_that_used_no_filament(self, db_session: Session) -> None:
        job = self._job(db_session, filament_used_g=0.0, sha="0")

        assert print_results.record_spool_usage(db_session, job) is False

    def test_declines_when_spoolman_is_switched_off(self, db_session: Session) -> None:
        job = self._job(db_session, sha="1")

        assert print_results.record_spool_usage(db_session, job) is False

    def test_declines_when_writes_to_spoolman_are_switched_off(
        self, db_session: Session
    ) -> None:
        from app.services import runtime_config

        job = self._job(db_session, sha="2")
        runtime_config.set_spoolman_enabled(db_session, True)
        runtime_config.set_spoolman_write_enabled(db_session, False)

        # Reading a fleet's spools is not the same permission as changing them.
        assert print_results.record_spool_usage(db_session, job) is False

    def test_declines_when_moonrakers_own_hook_is_already_counting(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.services import runtime_config

        job = self._job(db_session, sha="3")
        self._enable(db_session, spoolman_write_enabled=True)
        runtime_config.set_spoolman_write_force(db_session, False)
        monkeypatch.setattr(spoolman, "active_spool_sync", lambda *_a: 5)

        # Writing again would decrement the same spool twice for one print.
        assert print_results.record_spool_usage(db_session, job) is False

    def test_writes_anyway_when_the_operator_forces_it(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.services import runtime_config

        job = self._job(db_session, sha="4")
        self._enable(db_session, spoolman_write_enabled=True)
        runtime_config.set_spoolman_write_force(db_session, True)
        monkeypatch.setattr(spoolman, "active_spool_sync", lambda *_a: 5)
        written: list[tuple] = []
        monkeypatch.setattr(
            spoolman, "use_spool_weight_sync", lambda *args: written.append(args)
        )

        assert print_results.record_spool_usage(db_session, job) is True
        assert written

    def test_reports_a_spoolman_that_refuses_the_write(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.services.spoolman import SpoolmanError

        job = self._job(db_session, sha="5")
        self._enable(db_session, spoolman_write_enabled=True, spoolman_write_force=True)

        def refused(*_args: object, **_kwargs: object):
            raise SpoolmanError("spool not found")

        monkeypatch.setattr(spoolman, "use_spool_weight_sync", refused)

        assert print_results.record_spool_usage(db_session, job) is False

    def test_never_lets_an_unexpected_spoolman_failure_reach_the_print(
        self, db_session: Session, monkeypatch
    ) -> None:
        job = self._job(db_session, sha="6")
        self._enable(db_session, spoolman_write_enabled=True, spoolman_write_force=True)

        def exploding(*_args: object, **_kwargs: object):
            raise RuntimeError("spoolman exploded")

        monkeypatch.setattr(spoolman, "use_spool_weight_sync", exploding)

        # A bookkeeping hiccup must not fail a print that already succeeded.
        assert print_results.record_spool_usage(db_session, job) is False

    def test_declines_when_spoolman_has_no_address_configured(
        self, db_session: Session
    ) -> None:
        from app.services import runtime_config

        job = self._job(db_session, sha="7")
        runtime_config.set_spoolman_enabled(db_session, True)
        runtime_config.set_spoolman_write_enabled(db_session, True)

        # Enabled but unconfigured is a half-set-up deployment, not an error.
        assert print_results.record_spool_usage(db_session, job) is False


class TestMaterialTypeForFile:
    def test_reports_the_material_the_slicer_recorded(
        self, db_session: Session
    ) -> None:
        row = _seed_file(db_session, sha="8")
        db_session.add(Metadata(file_id=row.id, material_type="PETG"))
        db_session.commit()

        assert print_results.material_type_for_file(db_session, row.id) == "PETG"

    def test_reports_nothing_for_a_file_with_no_metadata(
        self, db_session: Session
    ) -> None:
        row = _seed_file(db_session, sha="9")

        assert print_results.material_type_for_file(db_session, row.id) is None


class TestResolveCompletionCostWithoutMetadata:
    def test_reports_no_weight_when_neither_the_printer_nor_the_slicer_said(
        self, db_session: Session
    ) -> None:
        row = _seed_file(db_session, sha="A")
        job = build_print_job(db_session, row, remote_filename=row.original_filename)

        grams, cost = print_results.resolve_completion_cost(db_session, job)

        # No number is a better answer than a wrong one when planning spend.
        assert grams is None
        assert cost is None
