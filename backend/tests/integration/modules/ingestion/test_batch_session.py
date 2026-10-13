"""Batch execution boundaries preserve real durable receipts and terminal authority."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.db.models import IngestionEntryState, Job, JobState
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store
from app.modules.ingestion.batch_contracts import EntrySpec, JobBatch, LocalSource
from app.modules.ingestion.batch_session import BatchSession
from tests.factories.ops import build_job_context


@pytest.fixture
def batch_case(make_job, make_user):
    job = make_job(owner=make_user())
    ctx = build_job_context(job.id)
    factory = get_session_factory()
    commit = Mock(return_value=None)
    owner = JobBatch(job.id)
    batch = BatchSession(
        job_context=ctx, owner=owner, session_factory=factory, commit_entry=commit
    )
    spec = EntrySpec("selected", "part.stl", LocalSource("source"), 42)
    return batch, owner, factory, spec, commit


class TestBatchSession:
    @pytest.mark.parametrize("phase", ["new", "finished"], ids=str)
    def test_refuses_finish_outside_active_execution(self, batch_case, phase):
        batch, owner, factory, _, _ = batch_case
        if phase == "finished":
            batch.begin(0)
            batch.finish()
        with factory.scoped_session() as session:
            before = session.get(Job, owner.job_id).model_dump()
        with pytest.raises(RuntimeError, match="batch_session_not_active"):
            batch.finish()
        with factory.scoped_session() as session:
            assert session.get(Job, owner.job_id).model_dump() == before

    def test_refuses_repeated_begin(self, batch_case):
        batch, owner, factory, _, _ = batch_case
        batch.begin(None)
        with pytest.raises(RuntimeError, match="batch_session_already_started"):
            batch.begin(None)
        assert batch_store.results(owner, limit=256, session_factory=factory) == ()

    @pytest.mark.parametrize(
        "total", [-1, True, 1.0, "1"], ids=["negative", "bool", "float", "text"]
    )
    def test_refuses_invalid_discovery_total(self, batch_case, total):
        batch, owner, factory, _, _ = batch_case
        with pytest.raises(ValueError, match="invalid_batch_total"):
            batch.begin(total)
        assert batch_store.results(owner, limit=256, session_factory=factory) == ()
        batch.begin(0)
        assert batch.source_entries("source") == ()

    @pytest.mark.parametrize(
        "context",
        [{"source_selection_id": "selected"}, {"result_key": "child"}],
        ids=["selection-only", "result-only"],
    )
    def test_requires_paired_capture_context(self, batch_case, context):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(None)
        with pytest.raises(ValueError, match="capture_result_context_required"):
            batch.decorate(spec, **context)
        assert batch_store.results(owner, limit=256, session_factory=factory) == ()

    @pytest.mark.parametrize(
        "context",
        [
            {"member_title": ""},
            {"source_selection_id": True, "result_key": "child"},
            {"source_selection_id": "selected", "result_key": 1},
        ],
        ids=["empty-member", "bool-selection", "integer-result"],
    )
    def test_refuses_invalid_result_context(self, batch_case, context):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(None)
        with pytest.raises(ValueError, match="invalid_batch_result_context"):
            batch.decorate(spec, **context)
        assert batch_store.results(owner, limit=256, session_factory=factory) == ()

    def test_preserves_existing_result_context_on_mismatch(self, batch_case):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(1)
        batch.decorate(spec, member_title="original")
        with pytest.raises(ValueError, match="batch_result_context_mismatch"):
            batch.decorate(spec, member_title="replacement")
        batch.record_failure(spec, "source_unavailable")
        batch.finish()
        with factory.scoped_session() as session:
            job = session.get(Job, owner.job_id)
            assert job.state is JobState.FAILED
            assert json.loads(job.status_json)["result"]["items"] == [
                {
                    "name": "part.stl",
                    "error": "source_unavailable",
                    "member": "original",
                }
            ]

    @pytest.mark.parametrize("phase", ["known", "same-window"], ids=str)
    def test_refuses_changed_registered_snapshot(self, batch_case, phase):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(None)
        before = batch.register((spec,)) if phase == "known" else ()
        changed = replace(spec, display_name="renamed.stl")
        with pytest.raises(ValueError, match="batch_snapshot_mismatch"):
            batch.register((changed,) if phase == "known" else (spec, changed))
        assert batch_store.results(owner, limit=256, session_factory=factory) == before

    def test_refuses_superseding_changed_snapshot(self, batch_case):
        batch, _, _, spec, _ = batch_case
        batch.begin(None)
        original = batch.register((spec,))
        with pytest.raises(ValueError, match="batch_snapshot_mismatch"):
            batch.supersede(replace(spec, display_name="renamed.stl"))
        assert batch.source_entries("source") == original

    @pytest.mark.parametrize("error", ["", 1], ids=["empty", "integer"])
    def test_refuses_commit_without_failure_code(self, batch_case, tmp_path, error):
        batch, owner, factory, spec, commit = batch_case
        commit.return_value = {"error": error}
        batch.begin(1)
        with pytest.raises(ValueError, match="batch_failure_code_required"):
            batch.consume(spec, (tmp_path / "part.stl", "stl"))
        (entry,) = batch_store.results(owner, limit=256, session_factory=factory)
        assert entry.state is IngestionEntryState.PENDING
        assert entry.model_id is None and entry.file_id is None

    @pytest.mark.parametrize(
        "result",
        [{}, {"model_id": True, "file_id": 1}, {"model_id": 1, "file_id": "1"}],
        ids=["missing", "bool-model", "text-file"],
    )
    def test_refuses_commit_without_artifact_identity(
        self, batch_case, tmp_path, result
    ):
        batch, owner, factory, spec, commit = batch_case
        commit.return_value = result
        batch.begin(1)
        with pytest.raises(ValueError, match="batch_commit_identity_required"):
            batch.consume(spec, (tmp_path / "part.stl", "stl"))
        (entry,) = batch_store.results(owner, limit=256, session_factory=factory)
        assert entry.state is IngestionEntryState.PENDING
        assert entry.model_id is None and entry.file_id is None

    @pytest.mark.parametrize("error", ["", True], ids=["empty", "bool"])
    def test_refuses_empty_recorded_failure(self, batch_case, error):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(None)
        with pytest.raises(ValueError, match="batch_failure_code_required"):
            batch.record_failure(spec, error)
        assert batch_store.results(owner, limit=256, session_factory=factory) == ()

    def test_refuses_terminal_pending_entries(self, batch_case):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(1)
        original = batch.register((spec,))
        with pytest.raises(RuntimeError, match="batch_has_unprocessed_entries"):
            batch.finish()
        assert (
            batch_store.results(owner, limit=256, session_factory=factory) == original
        )
        with factory.scoped_session() as session:
            assert session.get(Job, owner.job_id).state is JobState.RUNNING

    def test_retains_outcome_when_declared_total_is_exceeded(self, batch_case):
        batch, owner, factory, spec, _ = batch_case
        batch.begin(0)
        with pytest.raises(ValueError, match="batch_total_mismatch"):
            batch.record_skipped(spec, "unsupported_file_type")
        (entry,) = batch_store.results(owner, limit=256, session_factory=factory)
        assert entry.state is IngestionEntryState.SKIPPED
        assert entry.error_code == "unsupported_file_type"
        batch.discovery_complete()
        batch.finish()
        with factory.scoped_session() as session:
            assert session.get(Job, owner.job_id).state is JobState.FAILED
