"""Real SQL snapshots, incremental outcomes and execution-authority fences."""

import json

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.cancellation import OperationCancelled
from app.db.models import (
    InboxItemState,
    IngestionEntry,
    IngestionEntryState,
    IngestRequestKind,
    JobKind,
)
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store, requests
from app.modules.ingestion.batch_contracts import (
    BatchCommitReference,
    EntrySpec,
    Failed,
    Imported,
    InboxBatch,
    JobBatch,
    LocalSource,
    Skipped,
)
from app.modules.work import service
from app.modules.work.contracts import JobExecution
from tests.factories import detached_file
from tests.factories.ops import build_job_context


@pytest.fixture
def execution(make_job, make_user):
    owner = make_user()
    job = make_job(owner=owner)
    return build_job_context(job.id), owner


@pytest.fixture
def spec():
    return EntrySpec("selected-file", "part.stl", LocalSource("source"), 42)


class TestFrozenEntries:
    def test_preserves_frozen_order_across_reordered_registration(
        self, execution, spec
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        other = EntrySpec("other", "other.stl", LocalSource("source"), None)
        first = batch_store.freeze_entries(ctx, owner, (spec, other))
        reversed_rows = batch_store.freeze_entries(ctx, owner, (other, spec))
        assert [row.id for row in reversed_rows] == [first[1].id, first[0].id]
        assert [row.ordinal for row in reversed_rows] == [1, 0]
        assert batch_store.counts(owner).total == 2

    def test_rejects_changed_source_snapshot(self, execution, spec):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        changed = EntrySpec(
            spec.identity,
            spec.display_name,
            LocalSource("replacement"),
            spec.size_bytes,
        )
        with pytest.raises(ValueError, match="batch_snapshot_mismatch"):
            batch_store.freeze_entries(ctx, owner, (changed,))
        assert batch_store.results(owner, limit=1) == original

    @pytest.mark.parametrize(
        "outcome,state,retryable",
        [
            (Failed("download_failed", True), IngestionEntryState.FAILED, True),
            (Skipped("unsupported"), IngestionEntryState.SKIPPED, False),
        ],
    )
    def test_records_terminal_unit_outcomes_incrementally(
        self, execution, spec, outcome, state, retryable
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        failed = batch_store.record_outcome(ctx, owner, row.key, outcome)
        assert failed.state is state
        assert failed.retryable is retryable
        assert failed.error_code is not None
        assert batch_store.counts(owner).processed == 1
        assert batch_store.counts(owner).pending == 0

    def test_excludes_stale_attempt_results_after_public_cancellation(
        self, execution, spec
    ):
        ctx, actor = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        service.cancel(ctx.job_id, actor=actor)
        with pytest.raises(OperationCancelled):
            batch_store.record_outcome(ctx, owner, row.key, Skipped("unsupported"))
        assert (
            batch_store.results(owner, limit=1)[0].state is IngestionEntryState.PENDING
        )

    def test_paginates_entry_results(self, execution, spec):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        other = EntrySpec("other", "other.stl", LocalSource("source"), None)
        rows = batch_store.freeze_entries(ctx, owner, (spec, other))
        assert batch_store.results(owner, limit=1) == (rows[0],)
        assert batch_store.results(owner, limit=1, after_id=rows[0].id) == (rows[1],)


class TestCommittedEntries:
    def test_recovers_the_committed_file_before_materialization(
        self, execution, spec, make_model, make_file
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        file = make_file(make_model(), ingestion_key=row.ingestion_key)
        recovered = batch_store.reconcile(ctx, owner, row.key)
        assert recovered is not None
        assert recovered.published and recovered.file_id == file.id
        assert recovered.model_id == file.model_id
        assert batch_store.counts(owner).succeeded == 1

    def test_preserves_success_when_a_later_attempt_reports_failure(
        self, execution, spec, make_model, make_file
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        file = make_file(make_model())
        imported = batch_store.record_outcome(
            ctx, owner, row.key, Imported(file.model_id, file.id)
        )
        assert (
            batch_store.record_outcome(
                ctx, owner, row.key, Failed("later_failure", True)
            )
            == imported
        )
        assert batch_store.counts(owner).failed == 0

    def test_rolls_back_confirmation_with_the_canonical_transaction(
        self, execution, spec, make_model, make_file
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        file = make_file(make_model())
        ref = BatchCommitReference(
            owner, row.id, JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch)
        )
        with get_session_factory().scoped_session() as session:
            batch_store.record_committed(session, ref, file, deduplicated=True)
            assert (
                session.exec(select(IngestionEntry)).one().state
                is IngestionEntryState.DEDUPLICATED
            )
            session.rollback()
        assert (
            batch_store.results(owner, limit=1)[0].state is IngestionEntryState.PENDING
        )

    def test_keeps_inbox_results_under_the_item_across_retry_jobs(
        self, make_user, make_job, make_inbox_item, spec, db_session
    ):
        actor = make_user()
        first = make_job(kind=JobKind.INGESTION_INBOX_IMPORT, owner=actor)
        item = make_inbox_item(actor, state=InboxItemState.IMPORTING, job_id=first.id)
        owner = InboxBatch(item.id)
        ctx = build_job_context(first.id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        replacement = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT, owner=actor, subject="replacement"
        )
        item.job_id = replacement.id
        db_session.add(item)
        db_session.commit()
        current = build_job_context(replacement.id)
        assert batch_store.freeze_entries(current, owner, (spec,)) == original
        with pytest.raises(OperationCancelled):
            batch_store.record_outcome(ctx, owner, spec.key, Failed("stale", True))


class _StatementProbe:
    """Capture actual statements without standing in for database execution."""

    def __init__(self):
        self.statements = []

    def before_execute(
        self, _connection, _cursor, statement, _parameters, _context, _many
    ):
        self.statements.append(statement)


class TestLegacyCandidates:
    def test_fresh_request_skips_legacy_file_lookup(
        self, make_user, make_model, make_file, db_session
    ):
        actor = make_user()
        request = requests.create(
            db_session,
            kind=IngestRequestKind.URL_SELECTION,
            owner_user_id=actor.id,
            selection={"_entry_identity_version": 0, "files": []},
        )
        db_session.commit()
        ctx = build_job_context(request.job_id)
        make_file(make_model(), ingestion_key=f"{request.job_id[:40]}:legacy-index")
        probe = _StatementProbe()
        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", probe.before_execute)

        try:
            candidates = batch_store.legacy_candidates(ctx, JobBatch(request.job_id))
        finally:
            event.remove(engine, "before_cursor_execute", probe.before_execute)

        assert candidates == ()
        assert [
            statement
            for statement in probe.statements
            if "from files" in statement.lower()
        ] == []
        assert (
            requests.identity_scheme(request)
            is requests.EntryIdentityScheme.STABLE_ENTRIES
        )
        assert requests.selection(request)["files"] == []

    @pytest.mark.parametrize("version", [None, True, "1", 0, 2])
    def test_rejects_invalid_request_identity_version(
        self, make_ingest_request, make_user, version
    ):
        request = make_ingest_request(
            make_user(), selection_json=json.dumps({"_entry_identity_version": version})
        )

        with pytest.raises(ValueError, match="entry_identity_version_invalid"):
            requests.identity_scheme(request)


class TestStoreBoundaries:
    def test_refuses_another_jobs_batch(self, execution, spec, make_job):
        ctx, actor = execution
        other = make_job(owner=actor, subject="other-batch")
        with pytest.raises(OperationCancelled):
            batch_store.freeze_entries(ctx, JobBatch(other.id), (spec,))
        assert batch_store.results(JobBatch(other.id), limit=1) == ()
        assert batch_store.results(JobBatch(ctx.job_id), limit=1) == ()

    @pytest.mark.parametrize(
        "state",
        [InboxItemState.CAPTURED, InboxItemState.COMPLETED],
        ids=["captured", "completed"],
    )
    def test_refuses_inactive_inbox_owner(
        self, execution, spec, make_inbox_item, state
    ):
        ctx, actor = execution
        item = make_inbox_item(actor, state=state, job_id=ctx.job_id)
        owner = InboxBatch(item.id)
        with pytest.raises(OperationCancelled):
            batch_store.freeze_entries(ctx, owner, (spec,))
        assert batch_store.results(owner, limit=1) == ()

    def test_refuses_unknown_entry_outcome(self, execution, spec):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        with pytest.raises(LookupError, match="batch_entry_missing"):
            batch_store.record_outcome(
                ctx, owner, "absent-entry", Skipped("unsupported")
            )
        assert batch_store.results(owner, limit=1) == original

    @pytest.mark.parametrize(
        "limit,cursor",
        [(0, 0), (1001, 0), (True, 0), (1.0, 0), (1, -1), (1, True), (1, "0")],
        ids=[
            "zero",
            "over-ceiling",
            "boolean-limit",
            "float-limit",
            "negative-cursor",
            "boolean-cursor",
            "text-cursor",
        ],
    )
    def test_refuses_invalid_result_page(self, execution, spec, limit, cursor):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        with pytest.raises(ValueError, match="invalid_entry_page"):
            batch_store.results(owner, limit=limit, after_id=cursor)
        assert batch_store.results(owner, limit=1) == original

    def test_refuses_confirmation_for_unknown_receipt(
        self, execution, spec, make_model, make_file
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        file = make_file(make_model())
        ref = BatchCommitReference(
            owner,
            original[0].id + 1,
            JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch),
        )
        with get_session_factory().scoped_session() as session:
            with pytest.raises(LookupError, match="batch_entry_missing"):
                batch_store.record_committed(session, ref, file, deduplicated=False)
        assert batch_store.results(owner, limit=1) == original

    def test_refuses_confirmation_for_unpersisted_file(self, execution, spec):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        original = batch_store.freeze_entries(ctx, owner, (spec,))
        ref = BatchCommitReference(
            owner,
            original[0].id,
            JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch),
        )
        file = detached_file()
        assert file.id is None
        with get_session_factory().scoped_session() as session:
            with pytest.raises(RuntimeError, match="batch_file_not_persisted"):
                batch_store.record_committed(session, ref, file, deduplicated=False)
        assert batch_store.results(owner, limit=1) == original

    def test_preserves_reconciled_success(self, execution, spec, make_model, make_file):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        file = make_file(make_model(), ingestion_key=row.ingestion_key)
        first = batch_store.reconcile(ctx, owner, row.key)
        assert first.file_id == file.id
        assert first.model_id == file.model_id
        assert first.state is IngestionEntryState.IMPORTED
        assert batch_store.reconcile(ctx, owner, row.key) == first

    def test_excludes_claimed_legacy_commit(
        self, execution, spec, make_model, make_file
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        claimed = make_file(make_model(), ingestion_key=f"{ctx.job_id[:40]}:claimed")
        unclaimed = make_file(
            make_model(), ingestion_key=f"{ctx.job_id[:40]}:unclaimed"
        )
        batch_store.record_outcome(
            ctx, owner, row.key, Imported(claimed.model_id, claimed.id)
        )
        candidates = batch_store.legacy_candidates(ctx, owner)
        assert len(candidates) == 1
        assert candidates[0].ingestion_key == unclaimed.ingestion_key
        assert candidates[0].sha256 == unclaimed.sha256

    @pytest.mark.parametrize(
        "deduplicated", [False, True], ids=["imported", "deduplicated"]
    )
    def test_records_canonical_confirmation(
        self, execution, spec, make_model, make_file, deduplicated
    ):
        ctx, _ = execution
        owner = JobBatch(ctx.job_id)
        row = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        file = make_file(make_model())
        ref = BatchCommitReference(
            owner, row.id, JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch)
        )
        with get_session_factory().scoped_session() as session:
            batch_store.record_committed(session, ref, file, deduplicated=deduplicated)
            session.commit()
        result = batch_store.results(owner, limit=1)[0]
        assert result.file_id == file.id
        assert result.model_id == file.model_id
        assert result.state is (
            IngestionEntryState.DEDUPLICATED
            if deduplicated
            else IngestionEntryState.IMPORTED
        )
