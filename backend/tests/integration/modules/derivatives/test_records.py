"""The lifecycle of derivative rows: which kinds an Artifact still needs.

A row's absence at the current recipe is what "pending" means; every state
answers whether the kind is satisfied for now. Ready and skipped are
satisfied until an administrator regenerates the kind. Cancelled is satisfied
(cancelling withdraws intent). Failed waits out an exponential backoff, and a
deterministic failure, or one that exhausted its attempts, is terminal until a
retry or recipe bump. In flight is satisfied unless a lost execution left it
there too long.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeRegeneration,
    DerivativeState,
    File,
    FileType,
    JobKind,
)
from app.modules.derivatives import records
from app.modules.derivatives.kinds import VIEWER_STL_RECIPE, group, recipes_for
from app.modules.derivatives.source import DerivativeSource, subject_key


@pytest.fixture
def mesh(make_model, make_file):
    return make_file(make_model(), filename="part.stl")


def _row(**fields) -> ArtifactDerivative:
    fields.setdefault("updated_at", utcnow())
    return ArtifactDerivative(
        file_id=1, kind=DerivativeKind.THUMBNAIL, recipe_version=1, **fields
    )


class TestSatisfied:
    def test_no_row_needs_work(self) -> None:
        assert records.satisfied(None, now=utcnow()) is False

    @pytest.mark.parametrize("state", [DerivativeState.READY, DerivativeState.SKIPPED])
    def test_a_finished_output_is_satisfied(self, state: DerivativeState) -> None:
        assert records.satisfied(_row(state=state), now=utcnow()) is True

    def test_an_output_older_than_a_regeneration_needs_work(self) -> None:
        now = utcnow()
        row = _row(state=DerivativeState.READY, updated_at=now - timedelta(hours=1))

        assert records.satisfied(row, now=now, regenerated_at=now) is False

    def test_cancelled_stays_withdrawn(self) -> None:
        assert records.satisfied(_row(state=DerivativeState.CANCELLED), now=utcnow())

    def test_a_failure_waiting_out_its_backoff_is_satisfied_for_now(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.FAILED,
            attempts=1,
            next_attempt_at=now + timedelta(minutes=1),
        )

        assert records.satisfied(row, now=now) is True

    def test_a_failure_whose_backoff_expired_needs_work(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.FAILED,
            attempts=1,
            next_attempt_at=now - timedelta(seconds=1),
        )

        assert records.satisfied(row, now=now) is False

    def test_an_exhausted_failure_is_terminal(self) -> None:
        row = _row(
            state=DerivativeState.FAILED, attempts=settings.derivative_max_attempts
        )

        assert records.satisfied(row, now=utcnow()) is True

    def test_work_in_flight_is_satisfied(self) -> None:
        assert records.satisfied(_row(state=DerivativeState.RUNNING), now=utcnow())

    def test_work_left_in_flight_too_long_needs_work(self) -> None:
        now = utcnow()
        row = _row(
            state=DerivativeState.RUNNING,
            updated_at=now - records.STALE_IN_FLIGHT - timedelta(seconds=1),
        )

        assert records.satisfied(row, now=now) is False


class TestNeeded:
    @pytest.mark.parametrize("state", [DerivativeState.READY, DerivativeState.FAILED])
    def test_reopens_viewer_receipts_from_the_previous_scene_recipe(
        self, db_session, make_model, make_file, make_derivative, state
    ):
        now = utcnow()
        artifact = make_file(
            make_model(),
            filename="required-extension.3mf",
            file_type=FileType.THREE_MF,
            viewer_requested_at=now,
        )
        previous = make_derivative(
            artifact,
            DerivativeKind.VIEWER_STL,
            recipe_version=1,
            state=state,
            attempts=settings.derivative_max_attempts,
            storage_key="old-viewer.stl" if state is DerivativeState.READY else None,
            failure_reason="invalid_source"
            if state is DerivativeState.FAILED
            else None,
            next_attempt_at=None,
        )
        viewer = group(JobKind.DERIVATIVES_VIEWER_STL)
        assert viewer.kinds == {DerivativeKind.VIEWER_STL: VIEWER_STL_RECIPE}

        assert DerivativeKind.VIEWER_STL not in records.rows_for(db_session, artifact)
        assert records.needed(db_session, artifact, viewer.kinds, now=now) == {
            DerivativeKind.VIEWER_STL
        }
        offered = DerivativeSource(viewer).pending(db_session, now=now, limit=1)
        assert [item.subject_key for item in offered] == [subject_key(artifact.id)]
        fresh = records.begin(
            db_session, artifact, DerivativeKind.VIEWER_STL, VIEWER_STL_RECIPE, now=now
        )
        db_session.commit()

        assert fresh.recipe_version == VIEWER_STL_RECIPE
        assert fresh.state is DerivativeState.RUNNING
        assert fresh.attempts == 1
        assert (
            records.rows_for(db_session, artifact)[DerivativeKind.VIEWER_STL].id
            == fresh.id
        )
        db_session.refresh(previous)
        assert previous.recipe_version == 1
        assert previous.state is state
        assert previous.attempts == settings.derivative_max_attempts

    def test_lists_the_kinds_still_owed(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.METADATA)

        needed = records.needed(
            db_session,
            mesh,
            recipes_for(mesh),
            now=utcnow(),
        )

        assert needed == {DerivativeKind.THUMBNAIL}

    def test_a_row_at_an_older_recipe_does_not_count(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        # A recipe bump is the code saying the output changed.
        make_derivative(mesh, DerivativeKind.THUMBNAIL, recipe_version=0)

        assert records.needed(
            db_session,
            mesh,
            {DerivativeKind.THUMBNAIL: recipes_for(mesh)[DerivativeKind.THUMBNAIL]},
            now=utcnow(),
        ) == {DerivativeKind.THUMBNAIL}

    def test_a_regeneration_makes_a_ready_kind_owed_again(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, updated_at=utcnow() - timedelta(hours=1)
        )
        db_session.add(
            DerivativeRegeneration(kind=DerivativeKind.THUMBNAIL, requested_at=utcnow())
        )
        db_session.commit()

        assert records.needed(
            db_session,
            mesh,
            {DerivativeKind.THUMBNAIL: recipes_for(mesh)[DerivativeKind.THUMBNAIL]},
            now=utcnow(),
        ) == {DerivativeKind.THUMBNAIL}


class TestBegin:
    def test_opens_a_running_attempt(self, db_session: Session, mesh) -> None:
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=utcnow(),
        )

        assert (row.state, row.attempts) == (DerivativeState.RUNNING, 1)

    def test_a_retry_counts_as_another_attempt(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            attempts=2,
            failure_reason="render_failed",
            next_attempt_at=utcnow(),
        )

        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=utcnow(),
        )

        assert (row.attempts, row.failure_reason, row.next_attempt_at) == (
            3,
            None,
            None,
        )


class TestOutcomes:
    def test_ready_records_what_was_published(self, db_session: Session, mesh) -> None:
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=utcnow(),
        )

        records.mark_ready(
            db_session,
            records.attempt(db_session, mesh, row),
            now=utcnow(),
            storage_key="thumbs/1.webp",
            output={"strategy": "full"},
            duration_ms=12,
            peak_rss_bytes=3 * 1024**3,
        )

        assert (row.state, row.storage_key) == (DerivativeState.READY, "thumbs/1.webp")
        assert row.output_json == '{"strategy":"full"}'
        assert row.peak_rss_bytes == 3 * 1024**3

    def test_cancelled_attempt_cannot_publish_late(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        db_session.commit()
        token = records.attempt(db_session, mesh, row)
        records.cancel(
            db_session, mesh, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
        )
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=utcnow(), storage_key="late.webp")
        db_session.rollback()

        db_session.expire_all()
        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.CANCELLED
        )

    def test_ready_without_a_key_keeps_the_published_one(
        self, db_session: Session, mesh
    ) -> None:
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.METADATA,
            recipes_for(mesh)[DerivativeKind.METADATA],
            now=utcnow(),
        )
        row.storage_key = "kept"

        records.mark_ready(
            db_session, records.attempt(db_session, mesh, row), now=utcnow()
        )

        assert row.storage_key == "kept"

    def test_skipped_records_why_nothing_was_produced(
        self, db_session: Session, mesh
    ) -> None:
        # The whole reason is kept: a truncated one could read as another code.
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=utcnow(),
        )

        records.mark_skipped(
            db_session, records.attempt(db_session, mesh, row), "x" * 100, now=utcnow()
        )

        assert (row.state, row.failure_reason) == (
            DerivativeState.SKIPPED,
            "x" * 100,
        )

    def test_a_transient_failure_backs_off_exponentially(
        self, db_session: Session, mesh
    ) -> None:
        now = utcnow()
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=now,
        )
        row.attempts = 3

        records.mark_failed(
            db_session,
            records.attempt(db_session, mesh, row),
            "storage",
            now=now,
            deterministic=False,
        )

        assert row.next_attempt_at == now + timedelta(
            seconds=settings.derivative_backoff_seconds * 4
        )

    def test_the_backoff_is_capped_at_a_day(self, db_session: Session, mesh) -> None:
        from app.core.config import _overlay

        _overlay["derivative_backoff_seconds"] = 86400
        now = utcnow()
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=now,
        )
        row.attempts = 2

        records.mark_failed(
            db_session,
            records.attempt(db_session, mesh, row),
            "storage",
            now=now,
            deterministic=False,
        )

        assert row.next_attempt_at == now + timedelta(days=1)

    def test_timeouts_stop_at_the_configured_attempt_limit(self, db_session, mesh):
        now = utcnow()
        row = None
        for _ in range(settings.derivative_max_attempts):
            row = records.begin(
                db_session,
                mesh,
                DerivativeKind.THUMBNAIL,
                recipes_for(mesh)[DerivativeKind.THUMBNAIL],
                now=now,
            )
            records.mark_failed(
                db_session,
                records.attempt(db_session, mesh, row),
                "timeout",
                now=now,
                deterministic=False,
                duration_ms=300_000,
                peak_rss_bytes=123_456,
            )
            db_session.commit()
            if row.next_attempt_at is not None:
                now = row.next_attempt_at
        assert row is not None
        assert row.attempts == settings.derivative_max_attempts
        assert row.next_attempt_at is None
        assert records.satisfied(row, now=now)
        assert row.duration_ms == 300_000
        assert row.peak_rss_bytes == 123_456

    def test_a_deterministic_failure_is_terminal_at_once(
        self, db_session: Session, mesh
    ) -> None:
        # Retrying bytes that cannot render only repeats the failure.
        row = records.begin(
            db_session,
            mesh,
            DerivativeKind.THUMBNAIL,
            recipes_for(mesh)[DerivativeKind.THUMBNAIL],
            now=utcnow(),
        )

        records.mark_failed(
            db_session,
            records.attempt(db_session, mesh, row),
            "invalid_source",
            now=utcnow(),
            deterministic=True,
        )

        assert row.attempts == settings.derivative_max_attempts
        assert row.next_attempt_at is None

    def test_a_lost_execution_fails_only_its_in_flight_rows(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.METADATA, state=DerivativeState.RUNNING)
        make_derivative(mesh, DerivativeKind.THUMBNAIL)

        assert records.fail_in_flight(db_session, mesh.id, "lost", now=utcnow()) == 1

        states = {
            row.kind: row.state
            for row in db_session.exec(select(ArtifactDerivative)).all()
        }
        assert states == {
            DerivativeKind.METADATA: DerivativeState.FAILED,
            DerivativeKind.THUMBNAIL: DerivativeState.READY,
        }


class TestWithdrawAndRetry:
    def test_cancelling_withdraws_every_unfinished_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.METADATA)

        records.cancel(
            db_session,
            mesh,
            recipes_for(mesh),
            now=utcnow(),
        )
        db_session.commit()

        states = {
            row.kind: row.state for row in records.rows_for(db_session, mesh).values()
        }
        assert states == {
            DerivativeKind.METADATA: DerivativeState.READY,
            DerivativeKind.THUMBNAIL: DerivativeState.CANCELLED,
        }

    def test_a_retry_forgets_every_unsuccessful_attempt(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.METADATA)
        make_derivative(
            mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED, exhausted=True
        )

        assert records.reset(db_session, mesh) == 1

        assert set(records.rows_for(db_session, mesh)) == {DerivativeKind.METADATA}

    def test_a_retry_can_target_one_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.METADATA, state=DerivativeState.CANCELLED)
        make_derivative(mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.CANCELLED)

        records.reset(
            db_session,
            mesh,
            {DerivativeKind.THUMBNAIL: recipes_for(mesh)[DerivativeKind.THUMBNAIL]},
        )

        assert set(records.rows_for(db_session, mesh)) == {DerivativeKind.METADATA}

    def test_invalidating_forgets_a_kind_whatever_its_state(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        # An audit found the output broken even though its row says ready.
        make_derivative(mesh, DerivativeKind.THUMBNAIL)
        make_derivative(mesh, DerivativeKind.THUMBNAIL, recipe_version=0)

        assert records.invalidate(db_session, mesh, [DerivativeKind.THUMBNAIL]) == 1

        remaining = db_session.exec(select(ArtifactDerivative)).all()
        assert [(row.kind, row.recipe_version) for row in remaining] == [
            (DerivativeKind.THUMBNAIL, 0)
        ]

    def test_invalidating_a_kind_that_does_not_apply_is_refused(
        self, db_session: Session, mesh
    ) -> None:
        # A mesh has no toolpath; asking to re-derive one is a caller's bug.
        with pytest.raises(ValueError, match="derivative_not_applicable:toolpath"):
            records.invalidate(db_session, mesh, [DerivativeKind.TOOLPATH])


class TestRowInvariants:
    @pytest.mark.parametrize(
        ("state", "fields"),
        [
            (DerivativeState.FAILED, {}),
            (DerivativeState.SKIPPED, {}),
            (DerivativeState.READY, {"failure_reason": "stale"}),
            (DerivativeState.READY, {"next_attempt_at": datetime(2030, 1, 1)}),
            (DerivativeState.RUNNING, {}),
            (DerivativeState.READY, {"attempt_token": "retired"}),
        ],
        ids=[
            "failed-without-reason",
            "skipped-without-reason",
            "ready-with-reason",
            "ready-with-retry",
            "running-without-attempt-token",
            "ready-with-attempt-token",
        ],
    )
    def test_the_database_refuses_a_row_its_state_contradicts(
        self, db_session: Session, mesh, state: DerivativeState, fields: dict
    ) -> None:
        assert mesh.id is not None
        db_session.add(
            ArtifactDerivative(
                file_id=mesh.id,
                kind=DerivativeKind.THUMBNAIL,
                recipe_version=1,
                state=state,
                **fields,
            )
        )

        with pytest.raises(IntegrityError, match="ck_artifact_derivatives"):
            db_session.commit()

    def test_cancelling_a_failure_clears_its_reason(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(mesh, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED)

        records.cancel(
            db_session,
            mesh,
            {DerivativeKind.THUMBNAIL: recipes_for(mesh)[DerivativeKind.THUMBNAIL]},
            now=utcnow(),
        )
        db_session.commit()

        (row,) = db_session.exec(select(ArtifactDerivative)).all()
        assert (row.state, row.failure_reason) == (DerivativeState.CANCELLED, None)


class TestRead:
    def test_reports_every_applicable_kind(
        self, db_session: Session, mesh, make_derivative
    ) -> None:
        make_derivative(
            mesh,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            failure_reason="render_failed",
            exhausted=True,
        )

        reads = {read.kind: read for read in records.read(db_session, mesh)}

        assert reads[DerivativeKind.METADATA].state == "pending"
        assert (
            reads[DerivativeKind.THUMBNAIL].state,
            reads[DerivativeKind.THUMBNAIL].failure_reason,
            reads[DerivativeKind.THUMBNAIL].retryable,
        ) == ("failed", "render_failed", True)


class TestClaim:
    def test_rejects_a_replaced_attempt(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        first = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, first)
        db_session.commit()
        replacement = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        replacement_token = replacement.attempt_token
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=utcnow())
        db_session.rollback()

        current = records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL]
        assert (current.state, current.attempt_token) == (
            DerivativeState.RUNNING,
            replacement_token,
        )

    def test_rejects_a_cancelled_attempt_after_immediate_retry(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, row)
        db_session.commit()
        db_session.expunge(row)
        records.cancel(
            db_session, mesh, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
        )
        db_session.commit()
        records.reset(db_session, mesh)
        db_session.commit()
        replacement = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        replacement_token = replacement.attempt_token
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=utcnow())
        db_session.rollback()

        current = records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL]
        assert (current.state, current.attempt_token) == (
            DerivativeState.RUNNING,
            replacement_token,
        )

    @pytest.mark.parametrize("target", ["artifact", "model"], ids=str)
    def test_rejects_a_trashed_subject(self, db_session, mesh, target):
        from app.db.models import Model

        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, row)
        subject = mesh if target == "artifact" else db_session.get(Model, mesh.model_id)
        subject.deleted_at = utcnow()
        db_session.add(subject)
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=utcnow())
        db_session.rollback()

        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.RUNNING
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("sha256", "b" * 64, id="changed-content"),
            pytest.param("path", "replacement.stl", id="changed-locator"),
            pytest.param("source_etag", "new-etag", id="changed-etag"),
            pytest.param("source_version_id", "new-version", id="changed-version"),
        ],
    )
    def test_rejects_a_changed_source(self, db_session, mesh, field, value):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, row)
        setattr(mesh, field, value)
        db_session.add(mesh)
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=utcnow())
        db_session.rollback()

        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.RUNNING
        )

    def test_rejects_failure_from_an_old_attempt(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, row)
        db_session.commit()
        db_session.expunge(row)
        records.cancel(
            db_session, mesh, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
        )
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_failed(
                db_session, token, "late_error", now=utcnow(), deterministic=True
            )
        db_session.rollback()

        current = records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL]
        assert (current.state, current.failure_reason) == (
            DerivativeState.CANCELLED,
            None,
        )

    def test_rejects_skipped_from_an_old_attempt(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = records.attempt(db_session, mesh, row)
        db_session.commit()
        db_session.expunge(row)
        records.cancel(
            db_session, mesh, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
        )
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.mark_skipped(db_session, token, "late_skip", now=utcnow())
        db_session.rollback()

        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.CANCELLED
        )

    def test_regeneration_rejects_an_in_flight_publication(
        self, db_session, mesh, make_derivative_group_regeneration, make_derivative
    ):
        from app.db.models import JobKind
        from app.modules.derivatives.kinds import group
        from app.modules.derivatives.source import pending_predicate

        recipe = recipes_for(mesh)[DerivativeKind.THUMBNAIL]
        make_derivative(mesh, DerivativeKind.METADATA)
        started = utcnow()
        row = records.begin(
            db_session, mesh, DerivativeKind.THUMBNAIL, recipe, now=started
        )
        token = records.attempt(db_session, mesh, row)
        db_session.commit()
        make_derivative_group_regeneration(
            JobKind.DERIVATIVES_MESH,
            DerivativeKind.THUMBNAIL,
            requested_at=started + timedelta(seconds=1),
        )

        with pytest.raises(records.AttemptSuperseded):
            records.mark_ready(db_session, token, now=started + timedelta(seconds=2))
        db_session.rollback()

        assert DerivativeKind.THUMBNAIL in records.needed(
            db_session,
            mesh,
            {DerivativeKind.THUMBNAIL: recipe},
            now=started + timedelta(seconds=2),
        )
        assert (
            db_session.exec(
                select(File).where(
                    File.id == mesh.id,
                    pending_predicate(
                        group(JobKind.DERIVATIVES_MESH),
                        db_session,
                        now=started + timedelta(seconds=2),
                    ),
                )
            )
            .one()
            .id
            == mesh.id
        )


class TestPublicationTransactions:
    def test_committed_cancellation_wins_over_a_waiting_publisher(
        self, publication_engine
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from tests.factories import build_file, build_model

        with Session(publication_engine) as session:
            file = build_file(session, build_model(session), filename="cancel.stl")
            session.expunge(file)
            recipe = recipes_for(file)[DerivativeKind.THUMBNAIL]
            row = records.begin(
                session, file, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
            )
            token = records.attempt(session, file, row)
            session.commit()
            records.cancel(
                session, file, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
            )
            session.flush()
            entered = Event()

            def publish():
                with Session(publication_engine) as other:
                    entered.set()
                    with pytest.raises(records.AttemptSuperseded):
                        records.mark_ready(other, token, now=utcnow())
                    other.rollback()
                    return records.rows_for(other, file)[DerivativeKind.THUMBNAIL].state

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(publish)
                assert entered.wait(10)
                session.commit()
                assert future.result(timeout=20) is DerivativeState.CANCELLED

    def test_committed_publication_survives_a_waiting_cancellation(
        self, publication_engine
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from tests.factories import build_file, build_model

        with Session(publication_engine) as session:
            file = build_file(session, build_model(session), filename="ready.stl")
            session.expunge(file)
            recipe = recipes_for(file)[DerivativeKind.THUMBNAIL]
            row = records.begin(
                session, file, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
            )
            token = records.attempt(session, file, row)
            session.commit()
            records.mark_ready(session, token, now=utcnow(), storage_key="ready.webp")
            session.flush()
            entered = Event()

            def cancel():
                with Session(publication_engine) as other:
                    entered.set()
                    records.cancel(
                        other, file, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
                    )
                    other.commit()
                    row = records.rows_for(other, file)[DerivativeKind.THUMBNAIL]
                    return row.state, row.storage_key

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(cancel)
                assert entered.wait(10)
                session.commit()
                assert future.result(timeout=20) == (
                    DerivativeState.READY,
                    "ready.webp",
                )

    def test_regeneration_commit_wins_over_waiting_publication(
        self, publication_engine
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from app.db.models import DerivativeGroupRegeneration, JobKind
        from app.modules.derivatives import policy
        from tests.factories import build_file, build_model

        with Session(publication_engine) as session:
            file = build_file(session, build_model(session), filename="regenerate.stl")
            session.expunge(file)
            row = records.begin(
                session,
                file,
                DerivativeKind.THUMBNAIL,
                recipes_for(file)[DerivativeKind.THUMBNAIL],
                now=utcnow(),
            )
            token = records.attempt(session, file, row)
            session.commit()
            policy.lock(session)
            session.add(
                DerivativeGroupRegeneration(
                    definition=JobKind.DERIVATIVES_MESH,
                    kind=DerivativeKind.THUMBNAIL,
                    requested_at=utcnow(),
                )
            )
            session.flush()
            entered = Event()

            def publish():
                with Session(publication_engine) as other:
                    entered.set()
                    with pytest.raises(records.AttemptSuperseded):
                        records.mark_ready(other, token, now=utcnow())
                    other.rollback()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(publish)
                assert entered.wait(10)
                session.commit()
                future.result(timeout=20)
            assert (
                records.rows_for(session, file)[DerivativeKind.THUMBNAIL].state
                is DerivativeState.RUNNING
            )

    @pytest.mark.postgres
    @pytest.mark.parametrize("publication_engine", ["postgresql"], indirect=True)
    def test_independent_publications_share_generation_lock(self, publication_engine):
        from sqlalchemy import text

        from tests.factories import build_file, build_model

        with Session(publication_engine) as session:
            attempts = []
            for name in ("first.stl", "second.stl"):
                file = build_file(session, build_model(session), filename=name)
                row = records.begin(
                    session,
                    file,
                    DerivativeKind.THUMBNAIL,
                    recipes_for(file)[DerivativeKind.THUMBNAIL],
                    now=utcnow(),
                )
                attempts.append(records.attempt(session, file, row))
                session.commit()
        with (
            Session(publication_engine) as first,
            Session(publication_engine) as second,
        ):
            records.mark_ready(first, attempts[0], now=utcnow())
            first.flush()
            # An exclusive singleton lock would make this independent publisher fail.
            second.execute(text("SET LOCAL lock_timeout = '1s'"))
            records.mark_ready(second, attempts[1], now=utcnow())
            second.commit()
            first.commit()
        with Session(publication_engine) as session:
            rows = session.exec(select(ArtifactDerivative)).all()
            assert [row.state for row in rows] == [
                DerivativeState.READY,
                DerivativeState.READY,
            ]


class TestCommittedContext:
    def test_context_survives_basic_output_commit(self, db_session, mesh):
        recipe = recipes_for(mesh)[DerivativeKind.METADATA]
        row = records.begin(
            db_session, mesh, DerivativeKind.METADATA, recipe, now=utcnow()
        )
        context = records.attempt(db_session, mesh, row)
        records.mark_ready(db_session, context, now=utcnow())
        db_session.commit()

        assert records.require_context(db_session, context).id == mesh.id
        with pytest.raises(records.AttemptSuperseded):
            records.claim_running(db_session, context)
        db_session.rollback()
        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.METADATA].state
            is DerivativeState.READY
        )

    @pytest.mark.parametrize("changed", ["source", "generation"])
    def test_committed_context_rejects_replaced_input(self, db_session, mesh, changed):
        recipe = recipes_for(mesh)[DerivativeKind.METADATA]
        row = records.begin(
            db_session, mesh, DerivativeKind.METADATA, recipe, now=utcnow()
        )
        context = records.attempt(db_session, mesh, row)
        records.mark_ready(db_session, context, now=utcnow())
        if changed == "source":
            mesh.source_etag = "new-source"
            db_session.add(mesh)
        else:
            db_session.add(
                DerivativeRegeneration(
                    kind=DerivativeKind.METADATA, requested_at=utcnow()
                )
            )
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            records.require_context(db_session, context)
        db_session.rollback()
        assert (
            records.rows_for(db_session, mesh)[DerivativeKind.METADATA].state
            is DerivativeState.READY
        )
