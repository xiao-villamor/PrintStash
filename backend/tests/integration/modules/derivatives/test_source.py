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
