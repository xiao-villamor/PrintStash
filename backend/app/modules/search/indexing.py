"""Leased mini-batches resume from committed vectors, with hash/cancel fences."""

from __future__ import annotations

import secrets
import struct
import time
from dataclasses import dataclass, replace
from datetime import timedelta

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.search.passages import SubjectType
from sqlalchemy import case, delete, literal, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import (
    EmbeddingSpace,
    IndexGeneration,
    PassageVector,
    SearchIndexFailure,
    SearchPassage,
    SearchReconciliationState,
)
from app.db.session import SessionFactory
from app.modules.inference.configuration import embedding_provider
from app.modules.search import configuration, generations, vector_index, vector_store
from app.modules.search.access import passage_in_scope
from app.modules.search.reconciliation import reconcile_partition
from app.modules.search.text_inputs import document_input
from app.modules.storage.capacity import CapacityManager, CapacityReservationHandle
from app.runtime import maintenance
from app.runtime.jobs import registry

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class WorkInput:
    passage_id: int
    content_hash: str
    value: EmbeddingInput
    truncated: bool
    copied_blob: bytes | None = None


def claim(session: Session) -> tuple[int, str] | None:
    if not configuration.settings(session).enabled:
        return None
    available = or_(
        IndexGeneration.lease_token.is_(None),
        IndexGeneration.lease_expires_at <= utcnow(),
    )
    ids = session.exec(
        select(IndexGeneration.id)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            col(IndexGeneration.state).in_(("active", "building")),
            col(EmbeddingSpace.profile).in_(
                ("semantic_text", "thumbnail", "multiview", "point_cloud")
            ),
            IndexGeneration.version_token.is_not(None),
            col(IndexGeneration.cancel_requested).is_(False),
            available,
        )
        .order_by(IndexGeneration.last_activity_at, IndexGeneration.id)
        .limit(8)
    ).all()
    for generation_id in ids:
        token = secrets.token_hex(24)
        result = session.connection().execute(
            update(IndexGeneration)
            .where(
                IndexGeneration.id == generation_id,
                available,
                col(IndexGeneration.cancel_requested).is_(False),
                col(IndexGeneration.state).in_(("active", "building")),
            )
            .values(
                lease_token=token,
                lease_expires_at=utcnow()
                + timedelta(seconds=generations.LEASE_SECONDS),
                last_activity_at=utcnow(),
            )
        )
        session.commit()
        if result.rowcount == 1:
            return generation_id, token
    return None


def release(session: Session, generation_id: int, token: str) -> None:
    session.exec(
        update(IndexGeneration)
        .where(
            IndexGeneration.id == generation_id, IndexGeneration.lease_token == token
        )
        .values(lease_token=None, lease_expires_at=None, last_activity_at=utcnow())
    )
    session.commit()


def pending(session: Session, generation: IndexGeneration) -> list[WorkInput]:
    space = generations.contract(session, generation)
    deferred = (
        select(SearchIndexFailure.id)
        .where(
            SearchIndexFailure.generation_id == generation.id,
            SearchIndexFailure.passage_id == SearchPassage.id,
            SearchIndexFailure.input_hash == SearchPassage.content_hash,
            or_(
                SearchIndexFailure.state == "quarantined",
                SearchIndexFailure.retry_after > utcnow(),
            ),
        )
        .exists()
    )
    statement = (
        select(SearchPassage)
        .where(
            SearchPassage.id.in_(generations.missing(session, generation.id, space)),
            ~deferred,
        )
        .order_by(SearchPassage.id)
        .limit(settings.embedding_batch_size)
    )
    rows = session.exec(
        statement.where(SearchPassage.id > generation.passage_after_id)
    ).all()
    if not rows and generation.passage_after_id:
        rows = session.exec(statement).all()
    work = []
    for passage in rows:
        value, truncated = document_input(space, passage.text)
        previous = session.exec(
            select(PassageVector.vector_blob)
            .join(IndexGeneration, IndexGeneration.id == PassageVector.generation_id)
            .where(
                IndexGeneration.space_id == generation.space_id,
                IndexGeneration.id != generation.id,
                PassageVector.passage_id == passage.id,
                PassageVector.input_hash == passage.content_hash,
                PassageVector.native_dimension == space.dimension,
                PassageVector.unit_kind == "passage",
            )
            .order_by(PassageVector.id.desc())
            .limit(1)
        ).first()
        work.append(
            WorkInput(
                passage.id,
                passage.content_hash,
                value,
                truncated or passage.truncated,
                previous,
            )
        )
    return work


def publish(
    session: Session,
    generation_id: int,
    token: str,
    item: WorkInput,
    vector: tuple[float, ...],
    *,
    copied: bool = False,
) -> bool:
    if not generations.lock_owned(session, generation_id, token):
        return False
    generation = session.exec(
        select(IndexGeneration).where(*generations.owned(generation_id, token))
    ).first()
    if generation is None or not configuration.settings(session).enabled:
        return False
    space = generations.contract(session, generation)
    current_source = session.exec(
        select(SearchPassage.id)
        .where(
            SearchPassage.id == item.passage_id,
            SearchPassage.content_hash == item.content_hash,
        )
        .with_for_update()
    ).first()
    if current_source is None:
        return False
    source = select(
        SearchPassage.subject_type,
        SearchPassage.subject_id,
        case(
            (SearchPassage.subject_type == "model", SearchPassage.subject_id),
            else_=None,
        ).label("model_id"),
        literal(None).label("file_id"),
        SearchPassage.id.label("passage_id"),
    ).where(
        SearchPassage.id == item.passage_id,
        SearchPassage.content_hash == item.content_hash,
        passage_in_scope(generations.eligible(session, space)),
        select(IndexGeneration.id)
        .where(*generations.owned(generation_id, token))
        .exists(),
    )
    written = vector_store.publish(
        session,
        generation_id=generation_id,
        space=space,
        unit_kind="passage",
        unit_key=f"passage:{item.passage_id}",
        input_hash=item.content_hash,
        vector=vector,
        source=source,
        truncated=item.truncated,
    )
    if written:
        generation.processed += 1
        generation.copied += int(copied)
        generation.truncated_count += int(item.truncated)
        generation.verified_at = None
        generation.phase = "backfill"
        generation.error_code = None
        generation.passage_after_id = item.passage_id
        session.add(generation)
        session.exec(
            delete(SearchIndexFailure).where(
                SearchIndexFailure.generation_id == generation_id,
                SearchIndexFailure.passage_id == item.passage_id,
            )
        )
    session.commit()
    return written


def record_failure(
    session: Session, generation_id: int, token: str, item: WorkInput, code: str
) -> None:
    if not generations.lock_owned(session, generation_id, token):
        return
    generation = session.exec(
        select(IndexGeneration).where(*generations.owned(generation_id, token))
    ).first()
    passage = session.get(SearchPassage, item.passage_id, populate_existing=True)
    if (
        generation is None
        or passage is None
        or passage.content_hash != item.content_hash
    ):
        return
    # Errors originate at the provider contract and remain machine codes.
    import re

    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
        code = "inference_failed"
    row = session.exec(
        select(SearchIndexFailure).where(
            SearchIndexFailure.generation_id == generation_id,
            SearchIndexFailure.passage_id == item.passage_id,
            SearchIndexFailure.input_hash == item.content_hash,
        )
    ).first()
    attempts = (row.attempts if row else 0) + 1
    insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
    values = dict(
        generation_id=generation_id,
        passage_id=item.passage_id,
        input_hash=item.content_hash,
        attempts=attempts,
        state="quarantined" if attempts >= MAX_ATTEMPTS else "retry",
        retry_after=utcnow() + timedelta(seconds=min(60, 2**attempts)),
        error_code=code,
    )
    statement = insert(SearchIndexFailure).values(**values)
    session.exec(
        statement.on_conflict_do_update(
            index_elements=["generation_id", "passage_id", "input_hash"], set_=values
        )
    )
    generation.error_code = code
    generation.passage_after_id = item.passage_id
    generation.verified_at = None
    session.add(generation)
    session.commit()


class IndexProcessor:
    def __init__(self, sessions: SessionFactory):
        self.sessions = sessions

    def _wait_for_foreground(self, context: InferenceContext) -> None:
        # No database transaction or compute slot is held here. A full ASGI
        # mutation includes upload staging and terminal cleanup, which sit
        # outside the persisted ingest job's pending/running interval.
        while maintenance.foreground_mutations_pending():
            context.remaining()
            if maintenance.restore_in_progress():
                raise EmbeddingError("inference_cancelled")
            time.sleep(0.01)

    def _context(self, generation_id: int, token: str) -> InferenceContext:
        checked_at = 0.0
        cancelled = False

        def check() -> bool:
            nonlocal checked_at, cancelled
            now = time.monotonic()
            if now - checked_at >= 0.25:
                checked_at = now
                with self.sessions.scoped_session() as session:
                    cancelled = (
                        not configuration.settings(session).enabled
                        or session.exec(
                            select(IndexGeneration.id).where(
                                *generations.owned(generation_id, token)
                            )
                        ).first()
                        is None
                    )
            return cancelled

        return InferenceContext.bounded(120, priority="background", cancelled=check)

    def _reconcile(self, session: Session, generation: IndexGeneration) -> None:
        kinds = tuple(SubjectType)
        kind = kinds[generation.reconcile_kind]
        key = f"g{generation.id}:{generation.reconcile_kind}"
        reconcile_partition(session, kind, checkpoint_key=key)
        cursor = session.exec(
            select(SearchReconciliationState).where(
                SearchReconciliationState.subject_type == key
            )
        ).one()
        if cursor.partition_after_id == 0 and cursor.orphan_after_id == 0:
            generation.reconcile_kind += 1
        if generation.reconcile_kind >= len(kinds):
            generation.reconcile_kind = 0
            generation.phase = (
                "backfill" if generation.phase == "reconcile" else "verify"
            )
        session.add(generation)
        session.commit()

    def _verify(
        self, generation_id: int, token: str, context: InferenceContext
    ) -> None:
        with self.sessions.scoped_session() as session:
            generation = session.get(IndexGeneration, generation_id)
            space = generations.contract(session, generation)
            old = (
                session.get(IndexGeneration, generation.replaces_generation_id)
                if generation.replaces_generation_id
                else None
            )
            provider = embedding_provider(session, space)
            same_space = old is not None and old.space_id == generation.space_id
        if not same_space:
            provider.embed(
                (
                    EmbeddingInput(
                        "text",
                        text=space.query_prefix + "PrintStash search readiness probe",
                    ),
                ),
                space,
                context=context,
            )
        with self.sessions.scoped_session() as session:
            if not generations.lock_owned(session, generation_id, token):
                return
            generations._lock_cutover(session, generation_id)
            generation = session.exec(
                select(IndexGeneration).where(*generations.owned(generation_id, token))
            ).first()
            if generation is None or not configuration.settings(session).enabled:
                return
            try:
                total, _ = generations.verify_counts(session, generation)
            except OperationError:
                generation.phase = "verify_failed"
                generation.error_code = "search_generation_incomplete"
            else:
                sample = session.exec(
                    select(PassageVector)
                    .where(
                        PassageVector.id.in_(
                            generations.current_vectors(session, generation_id, space)
                        )
                    )
                    .order_by(PassageVector.id)
                    .limit(1)
                ).first()
                if sample is not None:
                    vector = struct.unpack(f"<{space.dimension}f", sample.vector_blob)
                    result = vector_store.query(
                        session,
                        generation_id=generation_id,
                        space=space,
                        vector=vector,
                        allowed_ids=select(PassageVector.id).where(
                            PassageVector.id == sample.id
                        ),
                        limit=1,
                        states=("building", "active"),
                    )
                    if not result.items or result.items[0].score < 0.99:
                        raise EmbeddingError("search_smoke_failed")
                generation.phase = "ready"
                generation.error_code = None
                generation.verified_at = utcnow()
                generation.verified_count = total
            session.add(generation)
            session.commit()

    def work_one(self) -> bool:
        if maintenance.foreground_mutations_pending():
            return False
        with self.sessions.scoped_session() as session:
            if generations.prune_one(session):
                return True
            leased = claim(session)
            if leased is None:
                return False
        generation_id, token = leased
        context = self._context(generation_id, token)
        batch_reservation: CapacityReservationHandle | None = None
        try:
            from app.modules.search import visual_index, visual_sources

            with self.sessions.scoped_session() as session:
                claimed = session.get(IndexGeneration, generation_id)
                visual = (
                    generations.contract(session, claimed).profile
                    in visual_sources.PROFILES
                )
            if visual:
                visual_index.process(self.sessions, generation_id, token, context)
                return True
            with self.sessions.scoped_session() as session:
                generation = session.get(IndexGeneration, generation_id)
                if generation.phase in {"reconcile", "final_reconcile"}:
                    if not generations.lock_owned(session, generation_id, token):
                        return True
                    session.refresh(generation)
                    self._reconcile(session, generation)
                    return True
                space = generations.contract(session, generation)
                work = pending(session, generation)
                provider = embedding_provider(session, space)
                verify = not work and generation.phase == "verify"
                if verify and generation.index_state == "building":
                    vector_index.rebuild_partition(session, generation)
                    session.commit()
                    if generation.index_state == "building":
                        return True
                if not work and not verify:
                    if generation.state == "active" and generation.phase == "ready":
                        return False
                    if generation.state == "building" and generation.phase not in {
                        "ready",
                        "verify_failed",
                    }:
                        generation.phase = "final_reconcile"
                        generation.reconcile_kind = 0
                    elif generation.state == "active":
                        generation.phase = "ready"
                    session.add(generation)
                    session.commit()
                    return True
                if work:
                    total, indexed, _ = generations.counts(session, generation)
                    estimate = (max(0, total - indexed) + 128) * (
                        space.dimension * 4 * 3 + 1024
                    ) + 1024**2
                    capacity = generations.resources(session, space, estimate)
                    manager = CapacityManager(self.sessions)
                    if generation.state == "building" and generation.reservation_id:
                        CapacityReservationHandle(
                            manager, generation.reservation_id, capacity, ()
                        ).renew(capacity)
                    else:
                        batch_reservation = manager.reserve(
                            f"search-batch:{generation_id}:{token}", capacity
                        )
            if verify:
                self._verify(generation_id, token, context)
                return True
            remote = [item for item in work if item.copied_blob is None]
            outputs = {}
            truncations = {}
            if remote:
                try:
                    vectors = provider.embed(
                        tuple(item.value for item in remote), space, context=context
                    )
                    outputs = {
                        item.passage_id: vector
                        for item, vector in zip(remote, vectors, strict=True)
                    }
                    truncations = dict(
                        zip(
                            (item.passage_id for item in remote),
                            getattr(provider, "last_truncations", ()),
                            strict=False,
                        )
                    )
                except EmbeddingError as exc:
                    if exc.code == "embedding_compute_busy":
                        raise
                    if exc.code in {
                        "inference_cancelled",
                        "inference_timeout",
                        "inference_circuit_open",
                        "inference_rate_limited",
                        "inference_endpoint_failed",
                        "inference_network_unavailable",
                    }:
                        with self.sessions.scoped_session() as session:
                            for item in remote:
                                record_failure(
                                    session, generation_id, token, item, exc.code
                                )
                    else:
                        # Isolate a poison input instead of quarantining the
                        # healthy neighbors that happened to share its batch.
                        for item in remote:
                            context.remaining()
                            try:
                                outputs[item.passage_id] = provider.embed(
                                    (item.value,), space, context=context
                                )[0]
                                flags = getattr(provider, "last_truncations", ())
                                truncations[item.passage_id] = bool(flags and flags[0])
                            except EmbeddingError as individual:
                                if individual.code == "embedding_compute_busy":
                                    raise
                                with self.sessions.scoped_session() as session:
                                    record_failure(
                                        session,
                                        generation_id,
                                        token,
                                        item,
                                        individual.code,
                                    )
            for item in work:
                context.remaining()
                vector = (
                    struct.unpack(f"<{space.dimension}f", item.copied_blob)
                    if item.copied_blob is not None
                    else outputs.get(item.passage_id)
                )
                if vector is None:
                    continue
                self._wait_for_foreground(context)
                with self.sessions.scoped_session() as session:
                    publish(
                        session,
                        generation_id,
                        token,
                        replace(
                            item,
                            truncated=item.truncated
                            or truncations.get(item.passage_id, False),
                        ),
                        vector,
                        copied=item.copied_blob is not None,
                    )
            return True
        except (EmbeddingError, OperationError) as exc:
            with self.sessions.scoped_session() as session:
                generation = session.exec(
                    select(IndexGeneration).where(
                        *generations.owned(generation_id, token)
                    )
                ).first()
                if generation is not None:
                    generation.error_code = exc.code
                    if (
                        generation.phase == "verify"
                        and exc.code != "embedding_compute_busy"
                    ):
                        generation.phase = "verify_failed"
                    session.add(generation)
                    session.commit()
            return True
        finally:
            if batch_reservation is not None:
                batch_reservation.release()
            with self.sessions.scoped_session() as session:
                release(session, generation_id, token)
                generation = session.get(
                    IndexGeneration, generation_id, populate_existing=True
                )
                if (
                    generation
                    and generation.state == "building"
                    and generation.phase == "ready"
                    and generation.auto_activate
                ):
                    try:
                        generations.activate(
                            session, generation_id, generation.version_token
                        )
                    except OperationError:
                        session.rollback()
                elif (
                    generation and generation.job_id and generation.state == "building"
                ):
                    total, indexed, quarantine = generations.counts(session, generation)
                    registry.update(
                        generation.job_id,
                        state="running",
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
                elif generation and generation.job_id and generation.state == "active":
                    registry.update(
                        generation.job_id,
                        state="completed",
                        processed=generation.processed,
                        progress=100,
                        result={"generation_id": generation.id},
                    )
