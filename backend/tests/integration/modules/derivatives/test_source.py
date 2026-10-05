"""The derivative source: which Artifacts a group must derive, found by pulling.

Ingestion commits a bare Artifact and knows nothing about derivatives; this
source finds every gap with an anti-join against derivative rows at the current
recipes. So a recipe bump, a new kind or a "regenerate all" needs no backfill
code: the anti-join starts matching again.

A pass costs the same over a fully derived library of any size: it checks
Artifacts above its high-water mark first (a fresh upload is interactive), then
a rotating window of older ids, and never returns more than it was asked for.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    SENTINEL_FILE_HASH,
    DerivativeKind,
    DerivativeRegeneration,
    DerivativeState,
    FileType,
    JobKind,
    WorkPriority,
)
from app.modules.derivatives import source as source_module
from app.modules.derivatives.kinds import group
from app.modules.derivatives.source import DerivativeSource, file_id_of, subject_key

MESH = DerivativeSource(group(JobKind.DERIVATIVES_MESH))


def _pending(session: Session, *, limit: int = 50, source=MESH):
    return source.pending(session, now=utcnow(), limit=limit)


def _subjects(session: Session, **kw) -> list[str]:
    return [item.subject_key for item in _pending(session, **kw)]


@pytest.fixture
def mesh(make_model, make_file):
    def build(**fields):
        return make_file(make_model(), filename="part.stl", **fields)

    return build


class TestPending:
    @pytest.mark.parametrize(
        ("kind", "old_recipe", "other_kind"),
        [
            (DerivativeKind.METADATA, 10, DerivativeKind.THUMBNAIL),
            (DerivativeKind.THUMBNAIL, 9, DerivativeKind.METADATA),
            (DerivativeKind.METADATA, 11, DerivativeKind.THUMBNAIL),
            (DerivativeKind.THUMBNAIL, 10, DerivativeKind.METADATA),
        ],
    )
    @pytest.mark.parametrize("state", [DerivativeState.READY, DerivativeState.FAILED])
    def test_rederives_stl_outputs_from_the_previous_reader_recipe(
        self, db_session, mesh, make_derivative, kind, old_recipe, other_kind, state
    ):
        artifact = mesh()
        make_derivative(
            artifact,
            kind,
            recipe_version=old_recipe,
            state=state,
            exhausted=state is DerivativeState.FAILED,
            failure_reason="invalid_source"
            if state is DerivativeState.FAILED
            else None,
        )
        make_derivative(artifact, other_kind)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_rederives_previews_with_ambiguous_completeness(
        self, db_session, mesh, make_derivative
    ):
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            recipe_version=7,
            output_json='{"strategy":"fallback","complete":true}',
        )

        assert _subjects(db_session) == [subject_key(artifact.id)]

    @pytest.mark.parametrize(
        ("kind", "previous_recipe", "other_kind"),
        [
            (DerivativeKind.METADATA, 8, DerivativeKind.THUMBNAIL),
            (DerivativeKind.THUMBNAIL, 7, DerivativeKind.METADATA),
        ],
    )
    def test_rederives_outputs_from_the_previous_stl_reader_recipe(
        self, db_session, mesh, make_derivative, kind, previous_recipe, other_kind
    ):
        artifact = mesh()
        make_derivative(artifact, kind, recipe_version=previous_recipe)
        make_derivative(artifact, other_kind)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    @pytest.mark.parametrize(
        ("kind", "old_recipe", "other_kind"),
        [
            (DerivativeKind.METADATA, 7, DerivativeKind.THUMBNAIL),
            (DerivativeKind.THUMBNAIL, 6, DerivativeKind.METADATA),
        ],
    )
    def test_rederives_outputs_from_the_percentile_framing_recipe(
        self, db_session, mesh, make_derivative, kind, old_recipe, other_kind
    ):
        artifact = mesh()
        make_derivative(artifact, kind, recipe_version=old_recipe)
        make_derivative(artifact, other_kind)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_rederives_previews_from_the_axis_degeneracy_recipe(
        self, db_session, mesh, make_derivative
    ):
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, recipe_version=5)

        subjects = _subjects(db_session)

        assert subjects == [subject_key(artifact.id)]

    def test_rederives_terminal_fingerprint_cap_refusals(
        self, db_session, mesh, make_derivative
    ):
        artifact = mesh()
        make_derivative(
            artifact,
            DerivativeKind.METADATA,
            recipe_version=6,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
        )
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            recipe_version=4,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
        )

        subjects = _subjects(db_session)

        assert subjects == [subject_key(artifact.id)]

    def test_rederives_thumbnails_from_the_unfiltered_vertex_recipe(
        self, db_session, mesh, make_derivative
    ):
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, recipe_version=3)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_rederives_thumbnails_from_the_world_float32_recipe(
        self, db_session, mesh, make_derivative
    ):
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, recipe_version=2)

        subjects = _subjects(db_session)

        assert subjects == [subject_key(artifact.id)]

    def test_a_fresh_upload_is_interactive_work(
        self, db_session: Session, mesh
    ) -> None:
        artifact = mesh()

        (item,) = _pending(db_session)

        assert item.subject_key == subject_key(artifact.id)
        assert item.priority is WorkPriority.INTERACTIVE

    def test_an_old_artifact_is_backfill(self, db_session: Session, mesh) -> None:
        mesh(uploaded_at=utcnow() - timedelta(days=30))

        (item,) = _pending(db_session)

        assert item.priority is WorkPriority.BACKFILL

    def test_a_fully_derived_artifact_needs_nothing(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        assert _pending(db_session) == []

    def test_one_missing_kind_is_enough(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_a_recipe_bump_makes_every_artifact_pending_again(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=0)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, recipe_version=0)

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_a_regeneration_makes_ready_outputs_pending_again(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        earlier = utcnow() - timedelta(hours=1)
        make_derivative(artifact, DerivativeKind.METADATA, updated_at=earlier)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, updated_at=earlier)
        db_session.add(
            DerivativeRegeneration(kind=DerivativeKind.THUMBNAIL, requested_at=utcnow())
        )
        db_session.commit()

        assert _subjects(db_session) == [subject_key(artifact.id)]

    @pytest.mark.parametrize(
        ("fields", "pending"),
        [
            ({"state": DerivativeState.CANCELLED}, False),
            ({"state": DerivativeState.RUNNING}, False),
            ({"state": DerivativeState.FAILED, "exhausted": True}, False),
            ({"state": DerivativeState.SKIPPED}, False),
        ],
        ids=["cancelled", "in-flight", "exhausted", "skipped"],
    )
    def test_satisfied_states_are_not_offered(
        self, db_session: Session, mesh, make_derivative, fields, pending
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, **fields)

        assert bool(_pending(db_session)) is pending

    def test_a_failure_is_offered_again_once_its_backoff_expires(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            next_attempt_at=utcnow() - timedelta(seconds=1),
        )

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_a_failure_waiting_out_its_backoff_is_not_offered(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            next_attempt_at=utcnow() + timedelta(minutes=5),
        )

        assert _pending(db_session) == []

    def test_work_a_lost_execution_left_in_flight_is_offered_again(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        artifact = mesh()
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.RUNNING,
            updated_at=utcnow() - timedelta(hours=2),
        )

        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_a_trashed_artifact_is_not_derived(self, db_session: Session, mesh) -> None:
        mesh(deleted_at=utcnow())

        assert _pending(db_session) == []

    def test_an_external_library_sentinel_is_not_derived(
        self, db_session: Session, mesh
    ) -> None:
        mesh(sha256=SENTINEL_FILE_HASH)

        assert _pending(db_session) == []

    def test_another_groups_artifacts_are_not_offered(
        self, db_session: Session, make_model, make_file
    ) -> None:
        make_file(make_model(), filename="plate.gcode", file_type=FileType.GCODE)

        assert _pending(db_session) == []
        gcode = DerivativeSource(group(JobKind.DERIVATIVES_GCODE))
        assert len(_pending(db_session, source=gcode)) == 1

    def test_never_returns_more_than_asked(self, db_session: Session, mesh) -> None:
        for _ in range(5):
            mesh()

        assert len(_pending(db_session, limit=2)) == 2

    def test_offers_nothing_without_room(self, db_session: Session, mesh) -> None:
        mesh()

        assert _pending(db_session, limit=0) == []


class TestBoundedScan:
    def test_revisits_old_artifacts_through_the_rotating_window(
        self, db_session: Session, mesh, make_derivative, monkeypatch
    ) -> None:
        # Above the high-water mark a pass sees only new uploads; an old
        # Artifact whose recipe changed is found by the window instead.
        monkeypatch.setattr(source_module, "WINDOW", 2)
        artifacts = [mesh() for _ in range(4)]
        for artifact in artifacts:
            make_derivative(artifact, DerivativeKind.METADATA)
            make_derivative(artifact, DerivativeKind.THUMBNAIL)
        assert _pending(db_session) == []
        db_session.add(
            DerivativeRegeneration(kind=DerivativeKind.THUMBNAIL, requested_at=utcnow())
        )
        db_session.commit()

        found: set[str] = set()
        for _ in range(3):
            found |= set(_subjects(db_session))

        assert found == {subject_key(artifact.id) for artifact in artifacts}

    def test_costs_the_same_however_large_the_library(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        from app.db.session import get_session_factory

        def statements_for_one_pass() -> int:
            engine = db_session.get_bind()
            seen: list[str] = []

            def count(_conn, _cursor, statement, *_args):
                seen.append(statement)

            event.listen(engine, "before_cursor_execute", count)
            try:
                with get_session_factory().scoped_session() as session:
                    MESH.pending(session, now=utcnow(), limit=10)
            finally:
                event.remove(engine, "before_cursor_execute", count)
            return len(seen)

        def derived_library(size: int) -> None:
            for _ in range(size):
                artifact = mesh()
                make_derivative(artifact, DerivativeKind.METADATA)
                make_derivative(artifact, DerivativeKind.THUMBNAIL)

        # Each size gets a settling pass first: it moves the high-water mark
        # (and the first also creates the cursor), which is one write, not a
        # cost that grows with the library.
        derived_library(3)
        statements_for_one_pass()
        small = statements_for_one_pass()
        derived_library(30)
        statements_for_one_pass()

        # One query per stage, whatever the library holds: nothing per Artifact.
        assert statements_for_one_pass() == small


class TestNextDue:
    def test_is_the_earliest_failure_backoff_that_expires(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        now = utcnow()
        soon = now + timedelta(minutes=2)
        make_derivative(
            mesh(),
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            next_attempt_at=soon,
        )
        make_derivative(
            mesh(),
            DerivativeKind.METADATA,
            state=DerivativeState.FAILED,
            next_attempt_at=now + timedelta(hours=1),
        )

        due = MESH.next_due(db_session, now=now)

        assert due is not None
        assert abs((due - soon).total_seconds()) < 1

    def test_nothing_backing_off_has_no_due_time(self, db_session: Session) -> None:
        assert MESH.next_due(db_session, now=utcnow()) is None


class TestSubjects:
    def test_round_trips_an_artifact_id(self) -> None:
        assert file_id_of(subject_key(42)) == 42

    @pytest.mark.parametrize("subject", ["model/42", "file/", "file/x", "42"])
    def test_refuses_what_is_not_a_derivative_subject(self, subject: str) -> None:
        with pytest.raises(ValueError, match="not_a_derivative_subject"):
            file_id_of(subject)


class TestMeshContinuationSource:
    @staticmethod
    def _ready(artifact, make_derivative):
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

    def test_discovers_pending_analysis_after_basics_ready(
        self, db_session, mesh, make_derivative, make_mesh_continuation
    ):
        artifact = mesh()
        self._ready(artifact, make_derivative)
        make_mesh_continuation(artifact)
        assert _subjects(db_session) == [subject_key(artifact.id)]

    def test_waits_until_continuation_due(
        self, db_session, mesh, make_derivative, make_mesh_continuation
    ):
        artifact = mesh()
        self._ready(artifact, make_derivative)
        due = utcnow() + timedelta(minutes=3)
        make_mesh_continuation(artifact, available_at=due)
        now = utcnow()
        assert MESH.pending(db_session, now=now, limit=10) == []
        assert MESH.next_due(db_session, now=now) == due
        assert [
            item.subject_key for item in MESH.pending(db_session, now=due, limit=10)
        ] == [subject_key(artifact.id)]

    def test_discovers_obsolete_input_for_retirement(
        self, db_session, mesh, make_derivative, make_mesh_continuation
    ):
        artifact = mesh()
        self._ready(artifact, make_derivative)
        make_mesh_continuation(
            artifact, source_sha256="b" * 64, algorithm_version="historical-recipe"
        )
        assert _subjects(db_session) == [subject_key(artifact.id)]

    @pytest.mark.parametrize("hidden", ["trash", "wrong-type"])
    def test_ignores_inapplicable_continuation(
        self, db_session, mesh, make_derivative, make_mesh_continuation, hidden
    ):
        artifact = mesh()
        self._ready(artifact, make_derivative)
        make_mesh_continuation(artifact, available_at=utcnow() + timedelta(minutes=1))
        if hidden == "trash":
            artifact.deleted_at = utcnow()
        else:
            artifact.file_type = FileType.GCODE
        db_session.add(artifact)
        db_session.commit()
        assert _subjects(db_session) == []
        assert MESH.next_due(db_session, now=utcnow()) is None

    def test_bounds_continuation_discovery(
        self, db_session, mesh, make_derivative, make_mesh_continuation
    ):
        artifacts = [mesh() for _ in range(5)]
        for artifact in artifacts:
            self._ready(artifact, make_derivative)
            make_mesh_continuation(artifact)
        subjects = _subjects(db_session, limit=2)
        assert subjects == [subject_key(artifact.id) for artifact in artifacts[:2]]

    def test_job_retention_preserves_pending_analysis(
        self, db_session, mesh, make_derivative, make_mesh_continuation, make_job
    ):
        from app.db.models import JobState, MeshFingerprintContinuation

        artifact = mesh()
        self._ready(artifact, make_derivative)
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            state=JobState.COMPLETED,
            attempts=1,
            subject=subject_key(artifact.id),
        )
        pending = make_mesh_continuation(artifact, job=job)
        historical_id = job.id
        db_session.delete(job)
        db_session.commit()
        db_session.expire_all()
        current = db_session.get(MeshFingerprintContinuation, artifact.id)
        assert current is not None and current.token == pending.token
        assert current.job_id == historical_id
        assert _subjects(db_session) == [subject_key(artifact.id)]


class TestPriorityDiscovery:
    @staticmethod
    def pending(session, *, total=1, interactive=1, backfill=1):
        from app.modules.work.contracts import DiscoveryBudget

        # Reconstructing the source must preserve the durable scan and turn.
        source = DerivativeSource(group(JobKind.DERIVATIVES_MESH))
        return source.pending_prioritized(
            session,
            now=utcnow(),
            budget=DiscoveryBudget(
                total=total, interactive=interactive, backfill=backfill
            ),
        )

    def test_recent_upload_bypasses_an_old_unscanned_prefix(
        self, db_session, mesh, monkeypatch
    ):
        monkeypatch.setattr(source_module, "WINDOW", 2)
        for _ in range(5):
            mesh(uploaded_at=utcnow() - timedelta(days=30))
        recent = mesh()
        (item,) = self.pending(db_session, backfill=0)
        assert item.subject_key == subject_key(recent.id)
        assert item.priority is WorkPriority.INTERACTIVE

    def test_recent_upload_below_the_scan_cursor_remains_interactive(
        self, db_session, mesh
    ):
        from app.db.models import ReconcileCursor

        recent = mesh()
        db_session.add(
            ReconcileCursor(
                source=JobKind.DERIVATIVES_MESH,
                scan_high_water=recent.id,
                scan_position=recent.id,
            )
        )
        db_session.commit()
        (item,) = self.pending(db_session, backfill=0)
        assert item.subject_key == subject_key(recent.id)
        assert item.priority is WorkPriority.INTERACTIVE

    @pytest.mark.parametrize("state", ["QUEUED", "RUNNING", "INTERRUPTED"])
    def test_active_subjects_cannot_consume_the_result_limit(
        self, db_session, mesh, make_job, monkeypatch, state
    ):
        from app.db.models import JobState

        monkeypatch.setattr(source_module, "WINDOW", 2)
        pending = mesh()
        active = mesh()
        make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(active.id),
            state=JobState[state],
        )
        (item,) = self.pending(db_session, backfill=0)
        assert item.subject_key == subject_key(pending.id)

    def test_recent_pagination_passes_a_ready_candidate_prefix(
        self, db_session, mesh, make_derivative, monkeypatch
    ):
        monkeypatch.setattr(source_module, "WINDOW", 2)
        pending = mesh()
        for _ in range(5):
            ready = mesh()
            make_derivative(ready, DerivativeKind.METADATA)
            make_derivative(ready, DerivativeKind.THUMBNAIL)
        found = []
        for _ in range(4):
            items = self.pending(db_session, backfill=0)
            assert len(items) <= 1
            found.extend(items)
        assert subject_key(pending.id) in {item.subject_key for item in found}
        assert all(item.priority is WorkPriority.INTERACTIVE for item in found)

    def test_one_item_budget_gives_backfill_a_durable_turn(
        self, db_session, mesh, make_derivative
    ):
        old = mesh(uploaded_at=utcnow() - timedelta(days=30))
        priorities = []
        subjects = []
        for _ in range(2):
            mesh()
            (item,) = self.pending(db_session)
            priorities.append(item.priority)
            subjects.append(item.subject_key)
            artifact_id = file_id_of(item.subject_key)
            from app.db.models import File

            artifact = db_session.get(File, artifact_id)
            make_derivative(artifact, DerivativeKind.METADATA)
            make_derivative(artifact, DerivativeKind.THUMBNAIL)
        assert set(priorities) == {WorkPriority.INTERACTIVE, WorkPriority.BACKFILL}
        assert subject_key(old.id) in subjects

    def test_new_uploads_cannot_extend_an_old_rotation_forever(
        self, db_session, mesh, make_derivative, monkeypatch
    ):
        monkeypatch.setattr(source_module, "WINDOW", 2)
        old = [mesh(uploaded_at=utcnow() - timedelta(days=30)) for _ in range(5)]
        discovered = set()
        for _ in range(12):
            for _ in range(3):
                mesh()
            items = self.pending(db_session, interactive=0)
            assert len(items) <= 1
            for item in items:
                assert item.priority is WorkPriority.BACKFILL
                discovered.add(item.subject_key)
                artifact = next(
                    row for row in old if subject_key(row.id) == item.subject_key
                )
                make_derivative(artifact, DerivativeKind.METADATA)
                make_derivative(artifact, DerivativeKind.THUMBNAIL)
        assert discovered == {subject_key(item.id) for item in old}

    def test_full_backfill_quota_preserves_the_old_scan_round(
        self, db_session, mesh, monkeypatch
    ):
        from app.db.models import ReconcileCursor

        monkeypatch.setattr(source_module, "WINDOW", 2)
        old = [mesh(uploaded_at=utcnow() - timedelta(days=30)) for _ in range(3)]
        frozen_upper = old[-1].id
        db_session.add(
            ReconcileCursor(
                source=JobKind.DERIVATIVES_MESH,
                scan_high_water=frozen_upper,
                scan_position=0,
            )
        )
        db_session.commit()
        for _ in range(3):
            mesh()
            items = self.pending(db_session, backfill=0)
            assert len(items) == 1
            assert items[0].priority is WorkPriority.INTERACTIVE
            cursor = db_session.get(ReconcileCursor, JobKind.DERIVATIVES_MESH)
            assert cursor is not None
            assert cursor.scan_position == 0
            assert cursor.scan_high_water == frozen_upper
        (item,) = self.pending(db_session, interactive=0)
        assert item.subject_key == subject_key(old[0].id)
        assert item.priority is WorkPriority.BACKFILL
        cursor = db_session.get(ReconcileCursor, JobKind.DERIVATIVES_MESH)
        assert cursor is not None
        assert cursor.scan_high_water == frozen_upper
