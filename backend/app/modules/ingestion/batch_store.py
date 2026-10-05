"""Fenced durable entry snapshots and outcomes; no source I/O occurs here."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, func
from sqlmodel import Session, col, select

from app.core.cancellation import OperationCancelled
from app.core.time import utcnow
from app.db.models import (
    File,
    InboxItem,
    InboxItemState,
    IngestionEntry,
    IngestionEntryState,
    IngestRequest,
    JobState,
)
from app.db.session import SessionFactory, get_session_factory
from app.modules.work.contracts import JobContext, JobExecution
from app.modules.work.jobs import jobs

from . import requests
from .batch_contracts import (
    BatchCommitReference,
    BatchCounts,
    BatchOwner,
    Deduplicated,
    EntryOutcome,
    EntryRecord,
    EntrySpec,
    Failed,
    Imported,
    InboxBatch,
    JobBatch,
    LegacyCandidate,
    Skipped,
    decode_source_descriptor,
    encode_source_descriptor,
    ingestion_key,
)


def _owner(owner: BatchOwner) -> ColumnElement[bool]:
    if isinstance(owner, JobBatch):
        return col(IngestionEntry.job_id) == owner.job_id
    return col(IngestionEntry.inbox_item_id) == owner.item_id


def _authorize(
    session: Session, execution: JobExecution | JobContext, owner: BatchOwner
) -> None:
    current = jobs.lock_execution(
        session,
        execution.job_id,
        epoch=execution.execution_epoch,
        attempt=execution.attempt,
        states=(JobState.RUNNING,),
    )
    if current is None:
        raise OperationCancelled()
    if isinstance(owner, JobBatch):
        if owner.job_id != execution.job_id:
            raise OperationCancelled()
    else:
        inbox = session.exec(
            select(InboxItem)
            .where(InboxItem.id == owner.item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if (
            inbox is None
            or inbox.job_id != execution.job_id
            or inbox.state is not InboxItemState.IMPORTING
        ):
            raise OperationCancelled()


def _record(row: IngestionEntry, owner: BatchOwner) -> EntryRecord:
    if row.id is None:
        raise RuntimeError("entry_not_persisted")
    return EntryRecord(
        row.id,
        row.entry_key,
        ingestion_key(owner, row.entry_key),
        EntrySpec(
            row.identity,
            row.display_name,
            decode_source_descriptor(row.descriptor_json),
            row.size_bytes,
        ),
        row.ordinal,
        row.state,
        row.model_id,
        row.file_id,
        row.error_code,
        row.retryable,
    )


def _find(session: Session, owner: BatchOwner, key: str) -> IngestionEntry:
    row = session.exec(
        select(IngestionEntry).where(_owner(owner), IngestionEntry.entry_key == key)
    ).first()
    if row is None:
        raise LookupError("batch_entry_missing")
    return row


def freeze_entries(
    ctx: JobContext,
    owner: BatchOwner,
    specs: Sequence[EntrySpec],
    *,
    session_factory: SessionFactory | None = None,
) -> tuple[EntryRecord, ...]:
    """Freeze one bounded planned window before the caller materializes its bytes."""
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        _authorize(session, ctx, owner)
        last = session.exec(
            select(IngestionEntry.ordinal)
            .where(_owner(owner))
            .order_by(col(IngestionEntry.id).desc())
            .limit(1)
        ).first()
        ordinal = 0 if last is None else last + 1
        records = []
        for spec in specs:
            row = session.exec(
                select(IngestionEntry).where(
                    _owner(owner), IngestionEntry.entry_key == spec.key
                )
            ).first()
            descriptor = encode_source_descriptor(spec.descriptor)
            if row is None:
                row = IngestionEntry(
                    job_id=owner.job_id if isinstance(owner, JobBatch) else None,
                    inbox_item_id=owner.item_id
                    if isinstance(owner, InboxBatch)
                    else None,
                    entry_key=spec.key,
                    identity=spec.identity,
                    display_name=spec.display_name,
                    descriptor_json=descriptor,
                    ordinal=ordinal,
                    size_bytes=spec.size_bytes,
                )
                ordinal += 1
                session.add(row)
                session.flush()
            elif (
                row.identity,
                row.display_name,
                row.descriptor_json,
                row.size_bytes,
            ) != (spec.identity, spec.display_name, descriptor, spec.size_bytes):
                raise ValueError("batch_snapshot_mismatch")
            records.append(_record(row, owner))
        session.commit()
        return tuple(records)


def _apply(row: IngestionEntry, outcome: EntryOutcome) -> None:
    if row.state in (IngestionEntryState.IMPORTED, IngestionEntryState.DEDUPLICATED):
        return
    if isinstance(outcome, (Imported, Deduplicated)):
        row.state = (
            IngestionEntryState.DEDUPLICATED
            if isinstance(outcome, Deduplicated)
            else IngestionEntryState.IMPORTED
        )
        row.model_id, row.file_id = outcome.model_id, outcome.file_id
        row.error_code, row.retryable = None, False
    elif isinstance(outcome, Failed):
        row.state = IngestionEntryState.FAILED
        row.error_code, row.retryable = outcome.error_code, outcome.retryable
    elif isinstance(outcome, Skipped):
        row.state = IngestionEntryState.SKIPPED
        row.error_code, row.retryable = outcome.reason, False
    else:
        raise TypeError("entry_outcome_required")
    row.updated_at = utcnow()


def record_outcome(
    ctx: JobContext,
    owner: BatchOwner,
    key: str,
    outcome: EntryOutcome,
    *,
    session_factory: SessionFactory | None = None,
) -> EntryRecord:
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        _authorize(session, ctx, owner)
        row = _find(session, owner, key)
        _apply(row, outcome)
        session.add(row)
        session.commit()
        return _record(row, owner)


def record_committed(
    session: Session,
    reference: BatchCommitReference,
    file: File,
    *,
    deduplicated: bool,
) -> None:
    """Record success in the canonical Artifact/provenance transaction, without committing."""
    _authorize(session, reference.execution, reference.owner)
    row = session.exec(
        select(IngestionEntry).where(
            _owner(reference.owner), IngestionEntry.id == reference.entry_id
        )
    ).first()
    if row is None:
        raise LookupError("batch_entry_missing")
    if file.id is None:
        raise RuntimeError("batch_file_not_persisted")
    outcome = (
        Deduplicated(file.model_id, file.id)
        if deduplicated
        else Imported(file.model_id, file.id)
    )
    _apply(row, outcome)
    session.add(row)
    session.flush()


def legacy_candidates(
    ctx: JobContext,
    owner: BatchOwner,
    *,
    session_factory: SessionFactory | None = None,
) -> tuple[LegacyCandidate, ...]:
    """Read unclaimed old indexed commits; callers must verify source bytes."""
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        _authorize(session, ctx, owner)
        request = session.get(IngestRequest, ctx.job_id)
        if request is not None and (
            requests.identity_scheme(request)
            is requests.EntryIdentityScheme.STABLE_ENTRIES
        ):
            session.commit()
            return ()
        claimed = set(
            session.exec(
                select(IngestionEntry.file_id).where(
                    _owner(owner), col(IngestionEntry.file_id).is_not(None)
                )
            ).all()
        )
        files = session.exec(
            select(File).where(
                col(File.ingestion_key).startswith(
                    f"{ctx.job_id[:40]}:", autoescape=True
                )
            )
        ).all()
        if files and len(ctx.job_id) > 40:
            raise ValueError("legacy_job_identity_ambiguous")
        candidates = []
        for file in files:
            if file.id in claimed:
                continue
            if file.ingestion_key is None:
                raise RuntimeError("legacy_ingestion_key_missing")
            candidates.append(
                LegacyCandidate(file.ingestion_key, file.original_filename, file.sha256)
            )
        session.commit()
        return tuple(candidates)


def reconcile(
    ctx: JobContext,
    owner: BatchOwner,
    key: str,
    *,
    legacy_keys: Sequence[str] = (),
    session_factory: SessionFactory | None = None,
) -> EntryRecord | None:
    """Recover a File commit before ledger acknowledgement; provenance dedup commits atomically."""
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        _authorize(session, ctx, owner)
        row = _find(session, owner, key)
        if row.state in (
            IngestionEntryState.IMPORTED,
            IngestionEntryState.DEDUPLICATED,
        ):
            session.commit()
            return _record(row, owner)
        keys = (ingestion_key(owner, key), *legacy_keys)
        file = session.exec(
            select(File)
            .where(col(File.ingestion_key).in_(keys))
            .order_by(col(File.id))
            .limit(1)
        ).first()
        if file is None:
            session.commit()
            return None
        if file.id is None:
            raise RuntimeError("batch_file_not_persisted")
        _apply(row, Imported(file.model_id, file.id))
        session.add(row)
        session.commit()
        return _record(row, owner)


confirmed = reconcile


def counts(
    owner: BatchOwner, *, session_factory: SessionFactory | None = None
) -> BatchCounts:
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        grouped = session.exec(
            select(IngestionEntry.state, func.count())
            .where(_owner(owner))
            .group_by(IngestionEntry.state)
        ).all()
    states = {state: count for state, count in grouped}
    return BatchCounts(
        sum(states.values()), *(states.get(state, 0) for state in IngestionEntryState)
    )


def results(
    owner: BatchOwner,
    *,
    limit: int,
    after_id: int = 0,
    session_factory: SessionFactory | None = None,
) -> tuple[EntryRecord, ...]:
    if (
        type(limit) is not int
        or not 1 <= limit <= 1000
        or type(after_id) is not int
        or after_id < 0
    ):
        raise ValueError("invalid_entry_page")
    factory = session_factory or get_session_factory()
    with factory.scoped_session() as session:
        rows = session.exec(
            select(IngestionEntry)
            .where(_owner(owner), col(IngestionEntry.id) > after_id)
            .order_by(col(IngestionEntry.id))
            .limit(limit)
        ).all()
        return tuple(_record(row, owner) for row in rows)
