"""AI Search background work: projection, indexing, repair, captions, expansion.

Every piece of search work is already durable intent in the database, so each
definition's source reads it rather than a loop polling for it:

- ``search.project`` drains ``SearchProjectionRequest`` rows, which library
  changes record in their own transaction (``content_changed``);
- ``search.generation`` is one Job per index generation an administrator
  proposed: it drives that generation until it is active (or cancelled), and
  is the Job the generation reports as its progress;
- ``search.index`` keeps active generations current (new passages, pruning of
  retired ones) between builds;
- ``search.repair`` is the periodic safety net main ran every loop turn:
  reconciling passages per subject kind, lexical and vector index repair, and
  the caption recipe proposal;
- ``search.caption`` is one Job per caption attempt (the caption's ``job_id``),
  and ``search.caption_queue`` queues captions the library now owes;
- ``search.expand`` computes sparse expansions for passages that need one.

The work units themselves (``IndexProcessor``, ``CaptionProcessor``,
``ExpansionProcessor``) keep their row leases: those fence publication by a
superseded attempt. The engine decides what runs and where; a lane bounds how
much at once. Units that yield to interactive traffic (``embedding_compute_busy``)
end their Job's drain early; the next nudge or tick resumes it.
"""

from __future__ import annotations

import time
from datetime import datetime

from printstash_core.search.passages import SubjectType
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.logging import get_logger
from app.core.time import ensure_utc
from app.db.models import (
    EmbeddingSpace,
    IndexGeneration,
    JobKind,
    LaneName,
    SearchIndexFailure,
    SearchPassage,
    SearchProjectionRequest,
    SubjectCaption,
    WorkPriority,
)
from app.db.session import get_session_factory
from app.modules.search.subjects import (
    caption_of,
    caption_subject,
    generation_of,
    generation_subject,
)
from app.modules.work.contracts import (
    JobContext,
    JobDefinition,
    JobOutcome,
    Step,
    WorkItem,
)
from app.modules.work.sources import ScheduleSource, interval_cron

logger = get_logger(__name__)

# One drain step does at most this much before handing the lane back; the
# completion nudge runs the next pass straight away while work remains.
_DRAIN_SECONDS = 30.0
_DRAIN_UNITS = 64
# How long a unit waits out a user's write in flight before its Job yields.
_DRAIN_YIELD_SECONDS = 5.0
_CAPTION_YIELD_SECONDS = 30.0
_ADMISSION_POLL_SECONDS = 0.25
_REPAIR_SECONDS = 300
_INDEXED_PROFILES = ("semantic_text", "thumbnail", "multiview", "point_cloud")


def _enabled(session: Session) -> bool:
    from app.modules.search import configuration

    return bool(configuration.settings(session).enabled)


def _admitted(unit) -> bool | None:
    """Run one unit as a write-capable operation; ``None`` when not admitted.

    Search definitions are not admitted as a whole (``mutating=False``): a
    build may run for hours, and a restore must not wait for it. Each unit is
    admitted on its own instead, so a restore drains between two units.
    """
    from app.runtime.maintenance import (
        begin_mutating_operation,
        end_mutating_operation,
        foreground_mutations_pending,
    )

    # Search backfill yields to a user's write in flight (an upload being
    # staged, a save), so interactive work never waits behind it.
    if foreground_mutations_pending() or not begin_mutating_operation():
        return None
    try:
        return bool(unit())
    finally:
        end_mutating_operation()


def _admitted_soon(ctx: JobContext, unit, *, patience: float) -> bool | None:
    """``_admitted``, waiting out a user's write in flight for ``patience`` seconds.

    A write in flight (an upload being staged) is short, and giving the lane
    back at once would end this Job only for the next pass to start another.
    A restore or migration holding maintenance is not waited for here: it can
    last far longer than a step should hold its lane.
    """
    from app.runtime.maintenance import foreground_mutations_pending

    deadline = time.monotonic() + patience
    retried = False
    while True:
        admitted = _admitted(unit)
        if admitted is not None:
            return admitted
        if not foreground_mutations_pending():
            # Maintenance holds it, unless the write finished just now.
            if retried:
                return None
            retried = True
            continue
        if ctx.cancelled() or time.monotonic() >= deadline:
            return None
        time.sleep(_ADMISSION_POLL_SECONDS)


def _drain(ctx: JobContext, unit) -> int:
    """Run ``unit`` until it reports no work, the budget ends or it is cancelled.

    Each unit is one bounded, checkpointed piece of work that returns whether
    it did anything. A failing unit ends the drain; the next pass retries it.
    """
    deadline = time.monotonic() + _DRAIN_SECONDS
    done = 0
    while done < _DRAIN_UNITS and time.monotonic() < deadline:
        if ctx.cancelled() or not _admitted_soon(
            ctx, unit, patience=_DRAIN_YIELD_SECONDS
        ):
            break
        done += 1
    ctx.update(processed=done)
    return done


# --- search.project ----------------------------------------------------------


class ProjectionSource:
    """Pending while a recorded library change is due to be projected."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        due = session.exec(
            select(SearchProjectionRequest.id)
            .where(SearchProjectionRequest.next_attempt_at <= now)
            .limit(1)
        ).first()
        return [WorkItem(subject_key="search/project")] if due is not None else []

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        later = session.exec(
            select(func.min(SearchProjectionRequest.next_attempt_at)).where(
                SearchProjectionRequest.next_attempt_at > now
            )
        ).first()
        return ensure_utc(later) if later is not None else None


def _project_unit() -> bool:
    from app.modules.search.projection import process_pending

    with get_session_factory().scoped_session() as session:
        changed = process_pending(session)
        session.commit()
    return bool(changed)


def _project(ctx: JobContext) -> None:
    _drain(ctx, _project_unit)


# --- search.index / search.generation --------------------------------------


def _generation_work(session: Session):
    """Generations whose index is not settled: building, or active but behind."""
    return (
        select(IndexGeneration.id)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            col(IndexGeneration.state).in_(("active", "building")),
            col(EmbeddingSpace.profile).in_(_INDEXED_PROFILES),
            IndexGeneration.version_token.is_not(None),
            col(IndexGeneration.cancel_requested).is_(False),
        )
    )


class IndexSource:
    """Pending while a settled generation has new work or one awaits pruning.

    An in-progress building generation has its own ``search.generation`` Job.
    A manually prepared one can become ready before activation; new content
    reopens a tracked build Job. Active generations keep indexing later passages.
    """

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        if not _enabled(session):
            return []
        behind = session.exec(
            select(IndexGeneration)
            .where(
                col(IndexGeneration.id).in_(_generation_work(session)),
                IndexGeneration.state == "active",
                col(IndexGeneration.phase).notin_(("ready", "verify_failed")),
            )
            .order_by(IndexGeneration.id)
            .limit(8)
        ).all()
        from app.modules.search import generations, visual_sources
        from app.modules.search.indexing import deferred_retry_at
        from app.modules.search.indexing import pending as owed_inputs

        actionable = False
        for generation in behind:
            if generation.phase == "backfill":
                space = generations.contract(session, generation)
                if space.profile not in visual_sources.PROFILES:
                    if (
                        not owed_inputs(session, generation)
                        and deferred_retry_at(session, generation, space) is not None
                    ):
                        continue
            actionable = True
            break
        if not actionable:
            actionable = self._owed_passage(session) is not None
        prunable = session.exec(
            select(IndexGeneration.id)
            .where(
                col(IndexGeneration.state).in_(("retired", "cancelled", "failed")),
                IndexGeneration.version_token.is_not(None),
                IndexGeneration.retain_until <= now,
            )
            .limit(1)
        ).first()
        if not actionable and prunable is None:
            return []
        return [WorkItem(subject_key="search/index")]

    @staticmethod
    def _owed_passage(session: Session) -> int | None:
        """A settled active or manually prepared generation missing a vector.

        Uses the unit's own work query, so a quarantined passage, or one
        waiting out its retry, is not reported as owed (which would start a
        Job that finds nothing, over and over).
        """
        from app.modules.search import generations, visual_sources
        from app.modules.search.indexing import pending as owed_inputs

        settled = session.exec(
            select(IndexGeneration).where(
                col(IndexGeneration.id).in_(_generation_work(session)),
                IndexGeneration.phase == "ready",
                (IndexGeneration.state == "active")
                | (
                    (IndexGeneration.state == "building")
                    & col(IndexGeneration.auto_activate).is_(False)
                ),
            )
        ).all()
        for generation in settled:
            space = generations.contract(session, generation)
            if space.profile in visual_sources.PROFILES:
                # Views index files, not passages: owed while one lacks its vector.
                owed = session.exec(
                    generations.missing(session, generation.id, space).limit(1)
                ).first()
            else:
                owed = owed_inputs(session, generation)
            if owed:
                return generation.id
        return None

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        retention = session.exec(
            select(func.min(IndexGeneration.retain_until)).where(
                col(IndexGeneration.state).in_(("retired", "cancelled", "failed")),
                IndexGeneration.version_token.is_not(None),
                IndexGeneration.retain_until > now,
            )
        ).first()
        retry = session.exec(
            select(func.min(SearchIndexFailure.retry_after))
            .join(
                IndexGeneration,
                IndexGeneration.id == SearchIndexFailure.generation_id,
            )
            .join(SearchPassage, SearchPassage.id == SearchIndexFailure.passage_id)
            .where(
                IndexGeneration.state == "active",
                IndexGeneration.phase == "backfill",
                SearchIndexFailure.state == "retry",
                SearchIndexFailure.retry_after > now,
                SearchIndexFailure.input_hash == SearchPassage.content_hash,
            )
        ).first()
        due = [ensure_utc(value) for value in (retention, retry) if value is not None]
        return min(due) if due else None


def _index_unit() -> bool:
    from app.modules.search.indexing import IndexProcessor

    return IndexProcessor(get_session_factory()).work_one()


def _index(ctx: JobContext) -> None:
    _drain(ctx, _index_unit)


class GenerationSource:
    """Every building generation owes its Job; a lost one is offered again."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        rows = session.exec(
            select(IndexGeneration.id, IndexGeneration.actor_id)
            .where(
                IndexGeneration.state == "building",
                col(IndexGeneration.phase).notin_(("ready", "verify_failed")),
            )
            .order_by(col(IndexGeneration.id))
            .limit(limit)
        ).all()
        return [
            WorkItem(
                subject_key=generation_subject(generation_id),
                owner_user_id=actor_id,
                priority=WorkPriority.INTERACTIVE,
            )
            for generation_id, actor_id in rows
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        return None


def _generation_progress(ctx: JobContext, generation_id: int) -> str | None:
    """Report the build's progress; its state once it left ``building``."""
    from app.modules.search import generations

    with get_session_factory().scoped_session() as session:
        generation = session.get(IndexGeneration, generation_id)
        if generation is None:
            return "missing"
        if generation.state != "building":
            return generation.state
        total, indexed, quarantine = generations.counts(session, generation)
        ctx.update(
            processed=indexed,
            total=total,
            failed=quarantine,
            progress=min(99, 100 * indexed / max(1, total)),
            label=generation.phase,
            result={
                "generation_id": generation_id,
                "error_code": generation.error_code,
            },
        )
        if generation.phase == "verify_failed":
            return "verify_failed"
        if generation.phase == "ready" and not generation.auto_activate:
            return "ready"
    return None


def _build(ctx: JobContext) -> None:
    """Drive one generation until it is active; its Job reports the progress.

    The build holds the search lane for as long as it runs, so each turn also
    projects one batch of library changes first: an upload made during an
    hours-long rebuild still becomes searchable.
    """
    generation_id = generation_of(ctx.subject_key)
    while not ctx.cancelled():
        _admitted(_project_unit)
        settled = _generation_progress(ctx, generation_id)
        if settled == "active":
            ctx.update(progress=100, result={"generation_id": generation_id})
            return
        if settled == "ready":
            ctx.update(progress=100, result={"generation_id": generation_id})
            return
        if settled == "verify_failed":
            ctx.finish(
                JobOutcome.FAILED,
                error="search_generation_verify_failed",
                retryable=False,
                result={"state": settled, "generation_id": generation_id},
            )
            return
        if settled is not None:
            ctx.finish(
                JobOutcome.FAILED,
                error=f"search_generation_{settled}",
                retryable=False,
                result={"state": settled, "generation_id": generation_id},
            )
            return
        if not _admitted(_index_unit):
            # Waiting on something outside this step: a restore holds
            # maintenance, another process holds the generation's lease, or
            # interactive traffic has priority.
            time.sleep(1.0)


def _cancel_generation(session: Session, subject: str) -> None:
    from app.core.errors import OperationError
    from app.modules.search import generations

    row = session.get(IndexGeneration, generation_of(subject))
    if row is None or row.state != "building" or row.version_token is None:
        return
    try:
        generations.cancel(session, row.id, row.version_token)  # type: ignore[arg-type]
    except OperationError:
        session.rollback()


# --- search.repair -----------------------------------------------------------


def _repair() -> dict[str, int]:
    """One bounded round of the drift repairs, then the caption recipe check."""
    from printstash_core.inference import EmbeddingError

    from app.core.errors import OperationError
    from app.modules.inference import model_cache
    from app.modules.inference.worker_pool import pool as model_workers
    from app.modules.search.generations import ensure_caption_recipe
    from app.modules.search.lexical_index import rebuild_partition
    from app.modules.search.reconciliation import reconcile_partition
    from app.modules.search.vector_index import repair_partition as repair_vectors

    sessions = get_session_factory()
    reconciled = 0
    with sessions.scoped_session() as session:
        if not _enabled(session):
            return {"reconciled": 0}
        model_workers.prune_idle(
            tuple(
                model.directory
                for model in model_cache.inventory()
                if model_cache.referenced(session, model)
            )
        )
        for kind in SubjectType:
            reconciled += reconcile_partition(session, kind, limit=1)
            session.commit()
        rebuild_partition(session)
        repair_vectors(session)
        session.commit()
    with sessions.scoped_session() as session:
        try:
            ensure_caption_recipe(session)
        except (OperationError, EmbeddingError):
            session.rollback()
            logger.debug("caption recipe proposal deferred")
    return {"reconciled": reconciled}


def _repair_step(ctx: JobContext) -> None:
    outcome: dict[str, object] = {}

    def unit() -> bool:
        outcome.update(_repair())
        return True

    if _admitted(unit) is None:
        outcome["deferred"] = "maintenance"
    ctx.update(result=outcome)


def _repair_cron(session: Session) -> str | None:
    return interval_cron(_REPAIR_SECONDS) if _enabled(session) else None


# --- search.caption / search.caption_queue ---------------------------------


def _caption_due(now: datetime):
    from sqlalchemy import or_

    return (
        (SubjectCaption.state == "generated")
        & (SubjectCaption.attempts < 3)
        & or_(
            SubjectCaption.phase == "pending",
            (SubjectCaption.phase == "running")
            & (SubjectCaption.lease_expires_at < now),
        )
        & or_(
            SubjectCaption.retry_after.is_(None),
            SubjectCaption.retry_after <= now,
        )
    )


class CaptionSource:
    """One Job per caption that is due; a failed attempt waits for its retry."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        from app.modules.search import captions

        provider, _ = captions.endpoint(session)
        if provider is None:
            return []
        rows = session.exec(
            select(SubjectCaption.id, SubjectCaption.actor_id)
            .where(_caption_due(now))
            .order_by(col(SubjectCaption.id))
            .limit(limit)
        ).all()
        return [
            WorkItem(subject_key=caption_subject(caption_id), owner_user_id=actor_id)
            for caption_id, actor_id in rows
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        later = session.exec(
            select(func.min(SubjectCaption.retry_after)).where(
                SubjectCaption.state == "generated",
                SubjectCaption.attempts < 3,
                SubjectCaption.retry_after > now,
            )
        ).first()
        return ensure_utc(later) if later is not None else None


def _caption(ctx: JobContext) -> None:
    from app.modules.search.caption_worker import CaptionOutcome, CaptionProcessor

    captioned: list[CaptionOutcome] = []

    def unit() -> bool:
        captioned.append(
            CaptionProcessor(get_session_factory()).work_on(
                caption_of(ctx.subject_key), job_id=ctx.job_id
            )
        )
        return True

    if _admitted_soon(ctx, unit, patience=_CAPTION_YIELD_SECONDS) is None:
        # Maintenance holds the vault, or a long user write outlasted the
        # wait; the caption stays due and the next pass starts a new attempt.
        ctx.finish(JobOutcome.FAILED, error="caption_deferred", retryable=True)
        return
    outcome = captioned[0]
    if outcome.error is not None:
        ctx.finish(
            JobOutcome.FAILED,
            error=outcome.error,
            retryable=outcome.retry,
            result={"caption_id": caption_of(ctx.subject_key)},
        )
        return
    ctx.update(result={"caption_id": caption_of(ctx.subject_key), **outcome.result})


def _cancel_caption(session: Session, subject: str) -> None:
    from app.modules.search.caption_worker import withdraw

    withdraw(session, caption_of(subject))


class CaptionQueueSource:
    """Pending while the library owes captions nobody queued yet."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        from app.modules.search.caption_worker import owed

        return [WorkItem(subject_key="search/caption_queue")] if owed(session) else []

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        return None


def _queue_captions(ctx: JobContext) -> None:
    from app.modules.search.caption_worker import sweep

    counted: list[int] = []

    def unit() -> bool:
        with get_session_factory().scoped_session() as session:
            counted.append(sweep(session))
            session.commit()
        return True

    _admitted(unit)
    queued = counted[0] if counted else 0
    ctx.update(result={"queued": queued})
    if queued:
        ctx.nudge(JobKind.SEARCH_CAPTION)


# --- search.expand -----------------------------------------------------------


class ExpansionSource:
    """Pending while a visible passage needs its sparse expansion (re)computed."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        from app.modules.search.expansion_worker import next_passage

        return [WorkItem(subject_key="search/expand")] if next_passage(session) else []

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        from app.modules.search.expansion_worker import next_retry

        return next_retry(session, now=now)


def _expand(ctx: JobContext) -> None:
    from app.modules.search.expansion_worker import ExpansionProcessor

    processor = ExpansionProcessor(get_session_factory())
    _drain(ctx, processor.work_one)


def definitions() -> list[JobDefinition]:
    # ``mutating=False`` throughout: every unit admits itself (``_admitted``).
    return [
        JobDefinition(
            name=JobKind.SEARCH_PROJECT,
            lane=LaneName.SEARCH,
            steps=(Step(f"{JobKind.SEARCH_PROJECT.value}.drain", _project),),
            source=ProjectionSource(),
            completion_nudges=(JobKind.SEARCH_PROJECT, JobKind.SEARCH_INDEX),
            mutating=False,
            drain=True,
            label="Search projection",
        ),
        JobDefinition(
            name=JobKind.SEARCH_GENERATION,
            lane=LaneName.SEARCH,
            steps=(Step(f"{JobKind.SEARCH_GENERATION.value}.build", _build),),
            source=GenerationSource(),
            cancel=_cancel_generation,
            retry=lambda _session, _subject: False,
            mutating=False,
            label="Search index builds",
        ),
        JobDefinition(
            name=JobKind.SEARCH_INDEX,
            lane=LaneName.SEARCH,
            steps=(Step(f"{JobKind.SEARCH_INDEX.value}.drain", _index),),
            source=IndexSource(),
            completion_nudges=(JobKind.SEARCH_INDEX,),
            mutating=False,
            drain=True,
            label="Search indexing",
        ),
        JobDefinition(
            name=JobKind.SEARCH_REPAIR,
            lane=LaneName.SEARCH,
            steps=(Step(f"{JobKind.SEARCH_REPAIR.value}.run", _repair_step),),
            source=ScheduleSource(JobKind.SEARCH_REPAIR, _repair_cron),
            completion_nudges=(JobKind.SEARCH_PROJECT, JobKind.SEARCH_INDEX),
            mutating=False,
            label="Search repair",
        ),
        JobDefinition(
            name=JobKind.SEARCH_CAPTION_QUEUE,
            lane=LaneName.CAPTIONS,
            steps=(
                Step(f"{JobKind.SEARCH_CAPTION_QUEUE.value}.sweep", _queue_captions),
            ),
            source=CaptionQueueSource(),
            mutating=False,
            drain=True,
            label="Caption queue",
        ),
        JobDefinition(
            name=JobKind.SEARCH_CAPTION,
            lane=LaneName.CAPTIONS,
            steps=(Step(f"{JobKind.SEARCH_CAPTION.value}.caption", _caption),),
            source=CaptionSource(),
            cancel=_cancel_caption,
            # A failed attempt comes back through the source after its backoff.
            retry=lambda _session, _subject: False,
            completion_nudges=(JobKind.SEARCH_CAPTION,),
            mutating=False,
            label="Captions",
        ),
        JobDefinition(
            name=JobKind.SEARCH_EXPAND,
            lane=LaneName.EXPANSION,
            steps=(Step(f"{JobKind.SEARCH_EXPAND.value}.drain", _expand),),
            source=ExpansionSource(),
            completion_nudges=(JobKind.SEARCH_EXPAND,),
            mutating=False,
            drain=True,
            label="Search expansion",
        ),
    ]
