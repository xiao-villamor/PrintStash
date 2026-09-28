"""Search work as Jobs: each source reads durable intent, each Job moves it on.

Library changes, proposed index builds and owed captions are rows; these tests
defend that each definition's source reports exactly that intent as pending
(and nothing else), that its Job drains it through the real units, that an
index build is one Job whose end is the generation's end, and that a restore
is never kept waiting by a long-running search Job.
"""

from __future__ import annotations

import functools
from datetime import timedelta

import pytest
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    IndexGeneration,
    JobKind,
    SearchProjectionRequest,
    SubjectCaption,
)
from app.db.projections import ContentSource
from app.modules.search import generations, jobs
from app.modules.work.contracts import JobOutcome, PassSubmission
from app.modules.work.jobs import jobs as job_rows
from app.modules.work.submission import nudge
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal


def _pending(source, session: Session) -> list:
    return list(source.pending(session, now=utcnow(), limit=10))


class TestProjectionSource:
    def test_a_due_change_is_pending(
        self, db_session: Session, make_search_projection_request, make_model
    ) -> None:
        make_search_projection_request(ContentSource("model", make_model().id))

        items = _pending(jobs.ProjectionSource(), db_session)

        assert [item.subject_key for item in items] == ["search/project"]

    def test_a_deferred_change_asks_to_be_woken(
        self, db_session: Session, make_search_projection_request, make_model
    ) -> None:
        later = utcnow() + timedelta(minutes=5)
        make_search_projection_request(
            ContentSource("model", make_model().id), next_attempt_at=later
        )
        source = jobs.ProjectionSource()

        assert _pending(source, db_session) == []
        due = source.next_due(db_session, now=utcnow())
        assert due is not None and abs((due - later).total_seconds()) < 1

    def test_nothing_recorded_is_nothing_pending(self, db_session: Session) -> None:
        source = jobs.ProjectionSource()

        assert _pending(source, db_session) == []
        assert source.next_due(db_session, now=utcnow()) is None


class TestProjectJob:
    def test_projects_every_recorded_change(
        self,
        db_session: Session,
        make_search_projection_request,
        make_model,
        work_engine,
    ) -> None:
        make_search_projection_request(ContentSource("model", make_model().id))

        nudge(JobKind.SEARCH_PROJECT)
        work_engine.drain()

        db_session.expire_all()
        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_a_library_change_nudges_it(
        self, db_session: Session, make_model, work_engine
    ) -> None:
        # The projection is bound in every process that changes the library.
        from app.bootstrap.lifecycle import bind_search, restore_search
        from app.db.projections import content_changed

        previous = bind_search()
        try:
            content_changed(db_session, "model", [make_model().id])
            db_session.commit()
        finally:
            restore_search(previous)

        passes = [
            execution
            for execution in work_engine.executions.values()
            if isinstance(execution.submission, PassSubmission)
            and execution.submission.source is JobKind.SEARCH_PROJECT
        ]
        assert len(passes) == 1


class TestIndexSource:
    def test_an_idle_index_is_not_pending(
        self, db_session: Session, generation_setup
    ) -> None:
        assert _pending(jobs.IndexSource(), db_session) == []

    def test_a_building_generation_is_owned_by_its_build_job(
        self, db_session: Session, generation_setup
    ) -> None:
        actor, endpoint = generation_setup
        generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )

        items = _pending(jobs.IndexSource(), db_session)

        assert items == []

    def test_an_active_embedding_retry_waits_until_due(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_model,
        make_search_passage,
        make_search_index_failure,
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()
        generation = db_session.get(IndexGeneration, proposal.id)
        model = make_model("Later component")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        due_at = utcnow() + timedelta(minutes=2)
        make_search_index_failure(
            generation, passage, state="retry", attempts=1, retry_after=due_at
        )
        generation.phase = "backfill"
        db_session.add(generation)
        db_session.commit()
        source = jobs.IndexSource()

        assert _pending(source, db_session) == []
        wake = source.next_due(db_session, now=utcnow())
        assert wake is not None and abs((wake - due_at).total_seconds()) < 1

    def test_a_failed_active_index_does_not_restart_until_retried(
        self, db_session: Session, generation_setup, healthy_embeddings, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()
        generation = db_session.get(IndexGeneration, proposal.id)
        generation.phase = "verify_failed"
        generation.error_code = "embedding_dimension_mismatch"
        db_session.add(generation)
        db_session.commit()

        assert _pending(jobs.IndexSource(), db_session) == []

    def test_a_due_active_embedding_retry_is_index_work(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_model,
        make_search_passage,
        make_search_index_failure,
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()
        generation = db_session.get(IndexGeneration, proposal.id)
        model = make_model("Due component")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        make_search_index_failure(
            generation,
            passage,
            state="retry",
            attempts=1,
            retry_after=utcnow() - timedelta(seconds=1),
        )
        generation.phase = "backfill"
        db_session.add(generation)
        db_session.commit()

        assert [
            item.subject_key for item in _pending(jobs.IndexSource(), db_session)
        ] == ["search/index"]

    def test_retrying_an_active_index_wakes_vector_work(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_model,
        make_search_passage,
        make_search_index_failure,
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()
        generation = db_session.get(IndexGeneration, proposal.id)
        model = make_model("Retried component")
        passage = make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        make_search_index_failure(generation, passage)

        generations.retry_quarantine(db_session, proposal.id, proposal.version_token)
        db_session.commit()
        work_engine.drain()

        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        assert generation.state == "active"
        total, indexed, quarantined = generations.counts(db_session, generation)
        assert (total, indexed, quarantined) == (2, 2, 0)

    def test_a_passage_projected_after_the_build_is_indexed(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_search_projection_request,
        make_model,
    ) -> None:
        # An active, settled generation is not done for good: every passage
        # projected later is owed its vector in it.
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=True
            ),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()
        make_search_projection_request(ContentSource("model", make_model().id))

        nudge(JobKind.SEARCH_PROJECT)
        work_engine.drain()

        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        assert generation is not None and generation.state == "active"
        space = generations.contract(db_session, generation)
        assert db_session.exec(generations.eligible(db_session, space)).first()
        assert (
            db_session.exec(
                generations.missing(db_session, proposal.id, space).limit(1)
            ).first()
            is None
        )

    def test_nothing_is_pending_while_search_is_off(
        self, db_session: Session, generation_setup
    ) -> None:
        from app.modules.search import configuration

        actor, endpoint = generation_setup
        generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()

        assert _pending(jobs.IndexSource(), db_session) == []


class TestGenerationJob:
    def test_a_proposal_is_a_queued_build_job(
        self, db_session: Session, generation_setup
    ) -> None:
        actor, endpoint = generation_setup

        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )

        status = job_rows.get(proposal.job_id)
        assert status is not None
        assert (status.kind, status.state, status.owner_user_id) == (
            JobKind.SEARCH_GENERATION,
            "queued",
            actor.id,
        )

    def test_the_build_completes_with_the_generation(
        self, db_session: Session, generation_setup, healthy_embeddings, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=True
            ),
        )
        db_session.commit()

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        db_session.expire_all()
        assert db_session.get(IndexGeneration, proposal.id).state == "active"
        status = job_rows.get(proposal.job_id)
        assert status is not None and status.state == "completed"
        assert status.result == {"generation_id": proposal.id}

    def test_a_manually_activated_build_releases_its_job_when_ready(
        self, db_session: Session, generation_setup, healthy_embeddings, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        db_session.commit()

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        status = job_rows.get(proposal.job_id)
        assert (generation.state, generation.phase) == ("building", "ready")
        assert status is not None and status.state == "completed"
        assert _pending(jobs.GenerationSource(), db_session) == []

    def test_a_manual_build_tracks_a_new_job_for_later_content(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_search_projection_request,
        make_model,
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=False
            ),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        make_search_projection_request(ContentSource("model", make_model().id))
        nudge(JobKind.SEARCH_PROJECT)
        work_engine.drain()

        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        assert generation.job_id != proposal.job_id
        assert generation.phase == "ready"
        total, indexed, _ = generations.counts(db_session, generation)
        assert total == indexed == 2
        status = job_rows.get(generation.job_id)
        assert status is not None and status.state == "completed"

    def test_a_quarantined_build_fails_its_job(
        self, db_session: Session, generation_setup, healthy_embeddings, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        healthy_embeddings.poison = "Assembly guide"
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        db_session.expire_all()
        generation = db_session.get(IndexGeneration, proposal.id)
        status = job_rows.get(proposal.job_id)
        assert (generation.state, generation.phase) == ("building", "verify_failed")
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "search_generation_verify_failed",
        )
        assert _pending(jobs.GenerationSource(), db_session) == []

    def test_retrying_a_failed_build_tracks_a_new_job(
        self, db_session: Session, generation_setup, healthy_embeddings, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        healthy_embeddings.poison = "Assembly guide"
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()
        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        healthy_embeddings.poison = None
        generations.retry_quarantine(db_session, proposal.id, proposal.version_token)
        db_session.commit()
        db_session.expire_all()
        resumed = db_session.get(IndexGeneration, proposal.id)
        assert resumed.job_id != proposal.job_id

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        db_session.expire_all()
        assert db_session.get(IndexGeneration, proposal.id).state == "active"
        status = job_rows.get(resumed.job_id)
        assert status is not None and status.state == "completed"

    def test_a_running_build_keeps_projecting_library_changes(
        self,
        db_session: Session,
        generation_setup,
        healthy_embeddings,
        work_engine,
        make_search_projection_request,
        make_model,
    ) -> None:
        # A build holds the one search lane for as long as it runs (hours on a
        # large library); a change recorded meanwhile must still be projected.
        actor, endpoint = generation_setup
        generations.prepare(
            db_session,
            actor,
            GenerationProposal(
                endpoint_id=endpoint.id, index_backend="numpy", auto_activate=True
            ),
        )
        db_session.commit()
        make_search_projection_request(ContentSource("model", make_model().id))

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        db_session.expire_all()
        assert db_session.exec(select(SearchProjectionRequest)).all() == []

    def test_a_cancelled_generation_fails_its_build(
        self, db_session: Session, generation_setup, work_engine
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        generations.cancel(db_session, proposal.id, proposal.version_token)
        db_session.commit()

        nudge(JobKind.SEARCH_GENERATION)
        work_engine.drain()

        status = job_rows.get(proposal.job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "search_generation_cancelled",
        )

    def test_cancelling_the_build_cancels_the_generation(
        self, db_session: Session, generation_setup
    ) -> None:
        from app.modules.work import service as work_service

        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()

        work_service.cancel(proposal.job_id, actor=actor)

        db_session.expire_all()
        assert db_session.get(IndexGeneration, proposal.id).state == "cancelled"

    def test_a_lost_build_is_offered_again(
        self, db_session: Session, generation_setup
    ) -> None:
        actor, endpoint = generation_setup
        proposal = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        db_session.commit()

        items = _pending(jobs.GenerationSource(), db_session)

        assert [(item.subject_key, item.owner_user_id) for item in items] == [
            (jobs.generation_subject(proposal.id), actor.id)
        ]


class TestRepairJob:
    def test_runs_on_a_schedule_only_while_search_is_on(
        self, db_session: Session, generation_setup
    ) -> None:
        from app.modules.search import configuration

        assert jobs._repair_cron(db_session) is not None
        configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()

        assert jobs._repair_cron(db_session) is None

    def test_repairs_every_subject_kind(
        self, db_session: Session, generation_setup, monkeypatch
    ) -> None:
        from app.modules.search import reconciliation

        seen = []
        monkeypatch.setattr(
            reconciliation,
            "reconcile_partition",
            lambda _session, kind, **_kw: seen.append(kind) or 0,
        )

        assert jobs._repair() == {"reconciled": 0}
        assert set(seen) == set(SubjectType)

    def test_waits_for_a_restore(self, generation_setup) -> None:
        from app.modules.work.contracts import JobContext  # noqa: F401
        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        recorded: dict = {}

        class Context:
            def update(self, **fields):
                recorded.update(fields)

        hold_restore_maintenance()
        try:
            jobs._repair_step(Context())  # type: ignore[arg-type]
        finally:
            end_restore_maintenance()

        assert recorded == {"result": {"deferred": "maintenance"}}


class TestDrain:
    """A drain step does bounded work and hands the lane back."""

    class _Context:
        def __init__(self) -> None:
            self.fields: dict = {}

        def cancelled(self) -> bool:
            return False

        def update(self, **fields) -> None:
            self.fields.update(fields)

    def test_stops_at_its_unit_budget(self) -> None:
        context = self._Context()

        done = jobs._drain(context, lambda: True)  # type: ignore[arg-type]

        assert done == jobs._DRAIN_UNITS
        assert context.fields == {"processed": jobs._DRAIN_UNITS}

    def test_stops_when_there_is_nothing_left(self) -> None:
        calls = []

        done = jobs._drain(self._Context(), lambda: calls.append(1) and False)  # type: ignore[arg-type]

        assert (done, calls) == (0, [1])

    def test_yields_to_a_users_write_in_flight(self) -> None:
        from app.runtime.maintenance import (
            begin_mutating_operation,
            end_mutating_operation,
        )

        ran = []
        assert begin_mutating_operation(foreground=True)
        try:
            admitted = jobs._admitted(lambda: ran.append(True) or True)
        finally:
            end_mutating_operation(foreground=True)

        assert (admitted, ran) == (None, [])

    def test_waits_out_a_short_user_write(self, monkeypatch) -> None:
        import threading

        from app.runtime.maintenance import (
            begin_mutating_operation,
            end_mutating_operation,
        )

        monkeypatch.setattr(jobs, "_ADMISSION_POLL_SECONDS", 0.01)
        units = iter([True, False])
        assert begin_mutating_operation(foreground=True)
        finished = threading.Timer(0.2, lambda: end_mutating_operation(foreground=True))
        finished.start()
        try:
            done = jobs._drain(self._Context(), lambda: next(units))  # type: ignore[arg-type]
        finally:
            finished.join()

        # One Job carries on after the write instead of ending for another.
        assert done == 1

    def test_yields_once_a_user_write_outlasts_its_patience(self, monkeypatch) -> None:
        from app.runtime.maintenance import (
            begin_mutating_operation,
            end_mutating_operation,
        )

        monkeypatch.setattr(jobs, "_DRAIN_YIELD_SECONDS", 0.05)
        monkeypatch.setattr(jobs, "_ADMISSION_POLL_SECONDS", 0.01)
        ran = []
        assert begin_mutating_operation(foreground=True)
        try:
            done = jobs._drain(self._Context(), lambda: ran.append(1) or True)  # type: ignore[arg-type]
        finally:
            end_mutating_operation(foreground=True)

        assert (done, ran) == (0, [])

    def test_does_not_wait_for_a_restore(self) -> None:
        import time

        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        hold_restore_maintenance()
        started = time.monotonic()
        try:
            done = jobs._drain(self._Context(), lambda: True)  # type: ignore[arg-type]
        finally:
            end_restore_maintenance()

        assert done == 0
        assert time.monotonic() - started < jobs._DRAIN_YIELD_SECONDS

    def test_does_no_index_work_without_consent(
        self, db_session: Session, generation_setup
    ) -> None:
        from app.modules.search import configuration

        actor, endpoint = generation_setup
        pending = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()

        assert jobs._index_unit() is False
        row = db_session.get(IndexGeneration, pending.id, populate_existing=True)
        assert (row.state, row.processed, row.lease_token) == ("building", 0, None)

    def test_keeps_each_repair_partition_small(
        self, db_session: Session, generation_setup, make_model, make_search_passage
    ) -> None:
        # One repair round advances each kind by one bounded page, so a large
        # library never holds SQLite's writer for a whole reconciliation.
        from app.db.models import SearchReconciliationState

        ids = []
        for index in range(40):
            model = make_model(f"Stored passage {index}")
            ids.append(model.id)
            make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        db_session.commit()

        jobs._repair()

        db_session.expire_all()
        state = db_session.exec(
            select(SearchReconciliationState).where(
                SearchReconciliationState.subject_type == "model"
            )
        ).one()
        assert sum(id <= state.partition_after_id for id in ids) <= 1
        assert sum(id <= state.orphan_after_id for id in ids) <= 1


class TestRegistration:
    def test_the_single_subject_sources_are_drains(self) -> None:
        # Their one subject returns whenever new work arrives; the resubmit
        # cooldown must not hold it back (it stalled a new upload's projection).
        drains = {
            definition.name for definition in jobs.definitions() if definition.drain
        }

        assert drains == {
            JobKind.SEARCH_PROJECT,
            JobKind.SEARCH_INDEX,
            JobKind.SEARCH_CAPTION_QUEUE,
            JobKind.SEARCH_EXPAND,
        }

    def test_every_search_definition_runs_on_a_search_lane(self) -> None:
        from app.db.models import LaneName

        lanes = {definition.name: definition.lane for definition in jobs.definitions()}

        assert set(lanes.values()) == {
            LaneName.SEARCH,
            LaneName.CAPTIONS,
            LaneName.EXPANSION,
        }
        assert lanes[JobKind.SEARCH_CAPTION] == LaneName.CAPTIONS

    def test_the_process_catalog_runs_every_ai_search_definition(self) -> None:
        # Model downloads belong to AI Search too: the consent flow requests them.
        from app.bootstrap.work import definitions

        names = {definition.name for definition in definitions()}

        assert {definition.name for definition in jobs.definitions()} | {
            JobKind.INFERENCE_MODEL_DOWNLOAD
        } <= names


class TestRestoreAdmission:
    """Search definitions admit each unit, so a restore drains between units."""

    def test_every_search_definition_admits_its_own_units(self) -> None:
        assert all(not definition.mutating for definition in jobs.definitions())

    def test_a_unit_is_not_run_while_a_restore_holds_maintenance(self) -> None:
        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        ran = []
        hold_restore_maintenance()
        try:
            admitted = jobs._admitted(lambda: ran.append(True) or True)
        finally:
            end_restore_maintenance()

        assert (admitted, ran) == (None, [])

    def test_an_admitted_unit_counts_as_a_mutation_while_it_runs(self) -> None:
        from app.runtime.maintenance import active_mutations

        seen = []

        assert jobs._admitted(lambda: seen.append(active_mutations()) or True)
        assert seen == [1] and active_mutations() == 0


@pytest.fixture
def owed_caption(
    db_session: Session, make_user, make_model, make_file, make_inference_endpoint
):
    from app.db.models import FileType
    from app.modules.search import captions, configuration

    actor = make_user(superuser=True)
    model = make_model("Anonymous part")
    make_file(model, file_type=FileType.STL)
    endpoint = make_inference_endpoint(kind="chat", supports_images=True)
    configuration.update(
        db_session,
        SearchSettings(
            enabled=True,
            captions_enabled=True,
            send_rendered_images=True,
            chat_endpoint_id=endpoint.id,
        ),
        actor_id=actor.id,
    )
    db_session.commit()
    subject = SearchSubject(SubjectType.MODEL, model.id)
    return actor, subject, captions


class TestCaptionJobs:
    def test_the_queue_is_pending_while_a_model_is_owed_a_caption(
        self, db_session: Session, owed_caption
    ) -> None:
        items = _pending(jobs.CaptionQueueSource(), db_session)

        assert [item.subject_key for item in items] == ["search/caption_queue"]

    def test_queueing_makes_one_caption_job_due(
        self, db_session: Session, owed_caption, work_engine
    ) -> None:
        nudge(JobKind.SEARCH_CAPTION_QUEUE)
        work_engine.drain()

        db_session.expire_all()
        caption = db_session.exec(select(SubjectCaption)).one()
        assert caption.job_id is not None
        status = job_rows.get(caption.job_id)
        assert status is not None and status.kind == JobKind.SEARCH_CAPTION

    def test_a_failed_attempt_fails_its_job_retryably(
        self, db_session: Session, owed_caption, work_engine, monkeypatch
    ) -> None:
        from app.modules.search import caption_worker

        def broken(*_args):
            raise RuntimeError("private-render-path")

        monkeypatch.setattr(
            caption_worker,
            "CaptionProcessor",
            functools.partial(caption_worker.CaptionProcessor, image_renderer=broken),
        )
        nudge(JobKind.SEARCH_CAPTION_QUEUE)
        work_engine.drain()

        db_session.expire_all()
        caption = db_session.exec(select(SubjectCaption)).one()
        status = job_rows.get(caption.job_id)
        assert status is not None
        assert (status.state, status.error, status.retryable) == (
            "failed",
            "caption_generation_failed",
            True,
        )
        # Due again only after its backoff, as a new attempt.
        assert caption.phase == "pending" and caption.retry_after is not None

    def test_a_held_restore_defers_the_attempt(self) -> None:
        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        finished: dict = {}

        class Context:
            subject_key = jobs.caption_subject(1)
            job_id = "caption-job"

            def cancelled(self) -> bool:
                return False

            def finish(self, outcome, **fields) -> None:
                finished.update(outcome=outcome, **fields)

        hold_restore_maintenance()
        try:
            jobs._caption(Context())  # type: ignore[arg-type]
        finally:
            end_restore_maintenance()

        # Retryable, and named for what happened: nothing was attempted.
        assert finished == {
            "outcome": JobOutcome.FAILED,
            "error": "caption_deferred",
            "retryable": True,
        }

    def test_cancelling_a_caption_job_withdraws_the_attempt(
        self, db_session: Session, owed_caption
    ) -> None:
        from app.modules.search.caption_worker import sweep

        sweep(db_session)
        db_session.commit()
        caption = db_session.exec(select(SubjectCaption)).one()

        jobs._cancel_caption(db_session, jobs.caption_subject(caption.id))
        db_session.commit()

        db_session.expire_all()
        withdrawn = db_session.get(SubjectCaption, caption.id)
        assert (withdrawn.phase, withdrawn.error_code) == (
            "failed",
            "caption_cancelled",
        )
        assert _pending(jobs.CaptionSource(), db_session) == []
