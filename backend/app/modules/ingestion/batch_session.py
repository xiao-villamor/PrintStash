"""One import execution: durable unit identity, progress and a single terminal result."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from enum import Enum, auto
from pathlib import Path
from typing import TypeAlias

from printstash_core.imports import StagedAsset

from app.core.cancellation import checkpoint
from app.db.models.types import IngestionEntryState
from app.db.session import SessionFactory
from app.modules.work.contracts import JobContext, JobOutcome
from app.modules.work.jobs import failure_of

from . import batch_store
from .batch_contracts import (
    BatchCounts,
    BatchOwner,
    Deduplicated,
    EntryRecord,
    EntrySpec,
    Failed,
    Imported,
    Skipped,
)

StagedInput: TypeAlias = tuple[Path, str] | StagedAsset
CommitEntry: TypeAlias = Callable[
    [EntryRecord, StagedInput, str | None, str | None], dict[str, object] | None
]


def import_failure_code(exc: BaseException) -> str:
    """Classify long safe diagnostics using the existing import failure code."""
    reason = failure_of(exc)
    return reason if len(reason) <= 128 else "import_failed"


class _Lifecycle(Enum):
    NEW = auto()
    ACTIVE = auto()
    FINISHED = auto()


class BatchSession:
    """Consume separately owned windows without resetting identity or progress."""

    def __init__(
        self,
        *,
        job_context: JobContext,
        owner: BatchOwner,
        session_factory: SessionFactory,
        commit_entry: CommitEntry,
        grouped: bool = False,
        collection: str | None = None,
    ) -> None:
        self.context = job_context
        self.owner = owner
        self.factory = session_factory
        self._commit = commit_entry
        self._grouped = grouped
        self._collection = collection
        self._state = _Lifecycle.NEW
        self._total: int | None = None
        self._decorations: dict[str, dict[str, object]] = {}
        self._records: dict[str, EntryRecord] = {}
        self._known: dict[str, EntryRecord] = {}
        self._sources: dict[str, dict[str, EntryRecord]] = {}
        self._states: Counter[IngestionEntryState] = Counter()

    def _active(self) -> None:
        if self._state is not _Lifecycle.ACTIVE:
            raise RuntimeError("batch_session_not_active")

    def begin(self, total: int | None) -> None:
        if self._state is not _Lifecycle.NEW:
            raise RuntimeError("batch_session_already_started")
        if total is not None and (type(total) is not int or total < 0):
            raise ValueError("invalid_batch_total")
        checkpoint(force=True)
        self._total = total
        self._state = _Lifecycle.ACTIVE
        after_id = 0
        while page := batch_store.results(
            self.owner, limit=256, after_id=after_id, session_factory=self.factory
        ):
            for record in page:
                self._cache(record)
            after_id = page[-1].id
        self._progress()

    def _cache(self, record: EntryRecord) -> None:
        self._known[record.key] = record
        self._sources.setdefault(record.spec.descriptor.source_id, {})[record.key] = (
            record
        )

    def _remember(self, record: EntryRecord) -> None:
        previous = self._records.get(record.key)
        if previous is not None:
            self._states[previous.state] -= 1
        self._states[record.state] += 1
        self._records[record.key] = record
        self._cache(record)

    def _counts(self) -> BatchCounts:
        return BatchCounts(
            len(self._records),
            self._states[IngestionEntryState.PENDING],
            self._states[IngestionEntryState.IMPORTED],
            self._states[IngestionEntryState.DEDUPLICATED],
            self._states[IngestionEntryState.FAILED],
            self._states[IngestionEntryState.SKIPPED],
        )

    def source_entries(self, source_id: str) -> tuple[EntryRecord, ...]:
        """Read one source's frozen metadata without reopening its bytes."""
        self._active()
        return tuple(self._sources.get(source_id, {}).values())

    def decorate(
        self,
        spec: EntrySpec,
        *,
        source_selection_id: str | None = None,
        result_key: str | None = None,
        member_title: str | None = None,
    ) -> None:
        """Attach known result context before either confirmed reuse or staging."""
        self._active()
        if (source_selection_id is None) != (result_key is None):
            raise ValueError("capture_result_context_required")
        context = self._decorations.setdefault(spec.key, {})
        for key, value in (
            ("source_selection_id", source_selection_id),
            ("result_key", result_key),
            ("member", member_title),
        ):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError("invalid_batch_result_context")
                if key in context and context[key] != value:
                    raise ValueError("batch_result_context_mismatch")
                context[key] = value

    def supersede(self, spec: EntrySpec) -> None:
        """Exclude an expanded source placeholder from this execution, retaining history."""
        self._active()
        checkpoint(force=True)
        known = self._known.get(spec.key)
        if known is not None and known.spec != spec:
            raise ValueError("batch_snapshot_mismatch")
        record = self._records.pop(spec.key, None)
        if record is not None:
            self._states[record.state] -= 1
        self._decorations.pop(spec.key, None)
        self._progress()

    def register(self, specs: Sequence[EntrySpec]) -> tuple[EntryRecord, ...]:
        self._active()
        checkpoint(force=True)
        missing: dict[str, EntrySpec] = {}
        for spec in specs:
            known = self._known.get(spec.key)
            if known is not None:
                if known.spec != spec:
                    raise ValueError("batch_snapshot_mismatch")
            elif spec.key in missing and missing[spec.key] != spec:
                raise ValueError("batch_snapshot_mismatch")
            else:
                missing[spec.key] = spec
        if missing:
            for record in batch_store.freeze_entries(
                self.context,
                self.owner,
                tuple(missing.values()),
                session_factory=self.factory,
            ):
                self._cache(record)
        for spec in specs:
            self._remember(self._known[spec.key])
        return tuple(self._records[spec.key] for spec in specs)

    def confirmed(
        self, spec: EntrySpec, *, legacy_keys: Sequence[str] = ()
    ) -> EntryRecord | None:
        self._active()
        self.register((spec,))
        record = batch_store.reconcile(
            self.context,
            self.owner,
            spec.key,
            legacy_keys=legacy_keys,
            session_factory=self.factory,
        )
        if record is not None:
            self._remember(record)
        if record is not None and record.published:
            if record.model_id is None or record.file_id is None:
                raise RuntimeError("batch_confirmed_artifact_missing")
            self._progress(current_item=spec.display_name)
            return record
        return None

    def consume(
        self,
        spec: EntrySpec,
        staged: StagedInput,
        *,
        source_url: str | None = None,
        member_title: str | None = None,
        legacy_keys: Sequence[str] = (),
    ) -> EntryRecord:
        self._active()
        checkpoint(force=True)
        frozen = self.register((spec,))[0]
        self.decorate(spec, member_title=member_title)
        if isinstance(staged, StagedAsset):
            self.decorate(
                spec,
                source_selection_id=staged.source_selection_id,
                result_key=staged.result_key,
            )
        recovered = self.confirmed(spec, legacy_keys=legacy_keys)
        if recovered is not None:
            return recovered
        result = self._commit(frozen, staged, source_url, member_title)
        if result is None:
            outcome = Skipped("unsupported_file_type")
        elif "error" in result:
            error = result["error"]
            if not isinstance(error, str) or not error:
                raise ValueError("batch_failure_code_required")
            outcome = Failed(error, True)
        else:
            model_id, file_id = result.get("model_id"), result.get("file_id")
            if type(model_id) is not int or type(file_id) is not int:
                raise ValueError("batch_commit_identity_required")
            outcome = (
                Deduplicated(model_id, file_id)
                if result.get("deduplicated") is True
                else Imported(model_id, file_id)
            )
        # Successful canonical commits already acknowledge in their transaction.
        # The idempotent store also supports ordinary direct-call test committers.
        record = batch_store.record_outcome(
            self.context, self.owner, spec.key, outcome, session_factory=self.factory
        )
        self._remember(record)
        self._progress(current_item=spec.display_name)
        return record

    def record_failure(self, spec: EntrySpec, error_code: str) -> EntryRecord:
        self._active()
        if not isinstance(error_code, str) or not error_code:
            raise ValueError("batch_failure_code_required")
        self.register((spec,))
        record = batch_store.record_outcome(
            self.context,
            self.owner,
            spec.key,
            Failed(import_failure_code(RuntimeError(error_code)), True),
            session_factory=self.factory,
        )
        self._remember(record)
        self._progress(current_item=spec.display_name)
        return record

    def record_skipped(self, spec: EntrySpec, reason: str) -> EntryRecord:
        self._active()
        self.register((spec,))
        record = batch_store.record_outcome(
            self.context,
            self.owner,
            spec.key,
            Skipped(reason),
            session_factory=self.factory,
        )
        self._remember(record)
        self._progress(current_item=spec.display_name)
        return record

    def discovery_complete(self) -> None:
        self._active()
        self._total = len(self._records)
        self._progress()

    def _progress(self, *, current_item: str | None = None) -> None:
        counts = self._counts()
        if self._total is not None and counts.processed > self._total:
            raise ValueError("batch_total_mismatch")
        progress = (
            counts.processed / self._total * 100
            if self._total is not None and self._total > 0
            else None
        )
        self.context.update(
            stage="ingesting",
            total_steps=self._total,
            total=self._total,
            progress=progress,
            processed=counts.processed,
            step=counts.processed,
            succeeded=counts.succeeded,
            deduplicated=counts.deduplicated,
            failed=counts.failed,
            skipped=counts.skipped,
            current_item=current_item,
        )

    def finish(self, *, failure_code: str | None = None) -> None:
        self._active()
        if failure_code is not None and (
            not isinstance(failure_code, str)
            or not failure_code
            or len(failure_code) > 128
        ):
            raise ValueError("invalid_batch_failure_code")
        checkpoint(force=True)
        # One canonical paged refresh at termination. Historical Inbox receipts
        # remain stored but are not part of a narrowed selection's result/counts.
        after_id = 0
        while page := batch_store.results(
            self.owner, limit=256, after_id=after_id, session_factory=self.factory
        ):
            for record in page:
                if record.key in self._records:
                    self._remember(record)
            after_id = page[-1].id
        counts = self._counts()
        if counts.pending:
            raise RuntimeError("batch_has_unprocessed_entries")
        items: list[dict[str, object]] = []
        for record in self._records.values():
            if record.state is IngestionEntryState.SKIPPED:
                continue
            item: dict[str, object] = {"name": record.spec.display_name}
            if record.published:
                item.update(
                    model_id=record.model_id,
                    file_id=record.file_id,
                    deduplicated=record.state is IngestionEntryState.DEDUPLICATED,
                )
            else:
                item["error"] = record.error_code
            item.update(self._decorations.get(record.key, {}))
            items.append(item)
        result: dict[str, object] = {
            "imported": counts.succeeded,
            "total": counts.total,
            "items": items,
        }
        if self._grouped:
            result.update(kind="collection_import", collection=self._collection)
        errors = {record["error"] for record in items if "error" in record}
        error = None
        if not counts.succeeded:
            error = (
                failure_code
                if failure_code is not None
                else "no_importable_files"
                if not counts.total or counts.skipped == counts.total
                else next(iter(errors))
                if self._grouped and len(errors) == 1
                else "collection_import_failed"
                if self._grouped
                else "import_failed"
            )
        checkpoint(force=True)
        self.context.finish(
            JobOutcome.COMPLETED if counts.succeeded else JobOutcome.FAILED,
            model_id=next(
                (item["model_id"] for item in items if "model_id" in item), None
            ),
            result=result,
            processed=counts.processed,
            total=counts.total,
            succeeded=counts.succeeded,
            deduplicated=counts.deduplicated,
            skipped=counts.skipped,
            failed=counts.failed,
            error=error,
            retryable=bool(counts.failed),
            failed_items=[
                {"name": item["name"], "reason": item["error"], "retryable": True}
                for item in items
                if "error" in item
            ],
        )
        self._state = _Lifecycle.FINISHED
