"""Batch counters and result scope operate on immutable receipt snapshots."""

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.db.models.types import IngestionEntryState
from app.db.session import SessionFactory
from app.modules.ingestion import batch_store
from app.modules.ingestion.batch_contracts import (
    EntryRecord,
    EntrySpec,
    InboxBatch,
    LocalSource,
)
from app.modules.ingestion.batch_session import BatchSession, import_failure_code
from app.modules.work.contracts import JobContext, JobOutcome


@pytest.fixture
def batch_case(monkeypatch):
    first = EntrySpec("first", "first.gcode", LocalSource("source"), None)
    second = EntrySpec("second", "second.gcode", LocalSource("source"), None)
    pending = EntryRecord(
        1,
        first.key,
        "first-key",
        first,
        0,
        IngestionEntryState.PENDING,
        None,
        None,
        None,
        False,
    )
    published = EntryRecord(
        2,
        second.key,
        "second-key",
        second,
        1,
        IngestionEntryState.IMPORTED,
        20,
        30,
        None,
        False,
    )
    context = Mock(spec=JobContext)
    factory = Mock(spec=SessionFactory)
    commit = Mock()
    session = BatchSession(
        job_context=context,
        owner=InboxBatch(1),
        session_factory=factory,
        commit_entry=commit,
    )
    monkeypatch.setattr(
        batch_store,
        "results",
        lambda *args, after_id=0, **kwargs: (
            (pending, published) if after_id == 0 else ()
        ),
    )
    monkeypatch.setattr(batch_store, "reconcile", lambda *args, **kwargs: published)
    return session, context, commit, first, second, pending, published


class TestBatchSession:
    def test_scopes_progress_to_current_selection(self, batch_case) -> None:
        session, context, commit, _, selected, historical, _ = batch_case
        session.begin(1)
        session.register((selected,))
        assert session.confirmed(selected) is not None
        session.finish()
        assert context.update.call_args.kwargs["processed"] == 1
        assert context.update.call_args.kwargs["total"] == 1
        assert context.update.call_args.kwargs["progress"] == 100
        assert context.finish.call_args.args == (JobOutcome.COMPLETED,)
        assert context.finish.call_args.kwargs["result"]["total"] == 1
        assert context.finish.call_args.kwargs["result"]["items"] == [
            {
                "name": "second.gcode",
                "model_id": 20,
                "file_id": 30,
                "deduplicated": False,
            }
        ]
        assert historical.state is IngestionEntryState.PENDING
        commit.assert_not_called()

    def test_reports_receipt_transitions_without_count_queries(
        self, batch_case, monkeypatch
    ) -> None:
        session, context, _, first, second, pending, published = batch_case
        count_query = Mock(side_effect=AssertionError("per-unit full ledger count"))
        monkeypatch.setattr(batch_store, "counts", count_query)
        transitioned = replace(
            pending, state=IngestionEntryState.IMPORTED, model_id=10, file_id=11
        )
        monkeypatch.setattr(
            batch_store, "reconcile", lambda *args, **kwargs: transitioned
        )
        session.begin(2)
        session.register((first, second))
        assert session.confirmed(first) == transitioned
        assert context.update.call_args.kwargs["processed"] == 2
        assert context.update.call_args.kwargs["succeeded"] == 2
        assert context.update.call_args.kwargs["progress"] == 100
        assert session.confirmed(first) == transitioned
        assert context.update.call_args.kwargs["processed"] == 2
        assert session.source_entries("source") == (transitioned, published)
        count_query.assert_not_called()

    def test_preserves_confirmed_capture_context(self, batch_case) -> None:
        session, context, commit, _, selected, _, _ = batch_case
        session.begin(1)
        session.register((selected,))
        session.decorate(
            selected,
            source_selection_id="selected-file",
            result_key="archive-child",
            member_title="member",
        )
        assert session.confirmed(selected) is not None
        session.finish()
        assert context.finish.call_args.kwargs["result"]["items"] == [
            {
                "name": "second.gcode",
                "model_id": 20,
                "file_id": 30,
                "deduplicated": False,
                "source_selection_id": "selected-file",
                "result_key": "archive-child",
                "member": "member",
            }
        ]
        commit.assert_not_called()


class TestImportFailure:
    @pytest.mark.parametrize(
        "message,expected", [("disk_full", "disk_full"), ("x" * 1000, "import_failed")]
    )
    def test_classifies_a_durable_unit_failure(self, message, expected) -> None:
        assert import_failure_code(RuntimeError(message)) == expected


@pytest.fixture(
    params=[
        pytest.param(
            (
                False,
                "url_not_a_direct_file",
                None,
                JobOutcome.FAILED,
                "url_not_a_direct_file",
            ),
            id="failed-source",
        ),
        pytest.param(
            (False, "x" * 128, None, JobOutcome.FAILED, "x" * 128), id="maximum-cause"
        ),
        pytest.param(
            (True, "url_not_a_direct_file", None, JobOutcome.COMPLETED, None),
            id="partial-success",
        ),
        pytest.param((False, "", ValueError, None, None), id="empty-cause"),
        pytest.param((False, "x" * 129, ValueError, None, None), id="oversized-cause"),
        pytest.param((False, True, ValueError, None, None), id="non-string-cause"),
    ]
)
def terminal_failure_case(request, batch_case, monkeypatch):
    include_success, failure_code, error_type, outcome, terminal_error = request.param
    session, context, _, first, second, pending, published = batch_case
    failed = replace(
        pending,
        state=IngestionEntryState.FAILED,
        error_code="download_failed",
        retryable=True,
    )
    records = (failed, published) if include_success else (failed,)
    monkeypatch.setattr(
        batch_store,
        "results",
        lambda *args, after_id=0, **kwargs: records if after_id == 0 else (),
    )
    session.begin(None)
    session.register((first, second) if include_success else (first,))
    session.discovery_complete()
    expected_exception = (
        pytest.raises(error_type, match="invalid_batch_failure_code")
        if error_type
        else nullcontext()
    )

    def terminal_result(actual):
        return (actual.args[0], actual.kwargs["error"]) if actual else (None, None)

    return (
        session,
        context,
        failure_code,
        expected_exception,
        (outcome, terminal_error),
        terminal_result,
    )


class TestScopedExpansion:
    def test_supersedes_a_placeholder_in_the_current_result_scope(
        self, batch_case, monkeypatch
    ) -> None:
        session, context, commit, placeholder, child, pending, published = batch_case
        failed = replace(
            pending,
            state=IngestionEntryState.FAILED,
            error_code="download_failed",
            retryable=True,
        )
        monkeypatch.setattr(
            batch_store,
            "results",
            lambda *args, after_id=0, **kwargs: (
                (failed, published) if after_id == 0 else ()
            ),
        )
        ledger_write = Mock(
            side_effect=AssertionError("supersede rewrote durable ledger")
        )
        monkeypatch.setattr(batch_store, "record_outcome", ledger_write)
        monkeypatch.setattr(batch_store, "freeze_entries", ledger_write)
        session.begin(None)
        session.register((placeholder, child))
        session.decorate(
            placeholder, source_selection_id="selection", result_key="self"
        )
        session.supersede(placeholder)
        session.supersede(placeholder)
        session.discovery_complete()
        session.finish()
        assert context.finish.call_args.args == (JobOutcome.COMPLETED,)
        assert context.finish.call_args.kwargs["failed"] == 0
        assert context.finish.call_args.kwargs["processed"] == 1
        assert context.finish.call_args.kwargs["total"] == 1
        assert context.finish.call_args.kwargs["result"]["items"] == [
            {
                "name": "second.gcode",
                "model_id": 20,
                "file_id": 30,
                "deduplicated": False,
            }
        ]
        assert session._known[placeholder.key] == failed
        assert session._sources["source"][placeholder.key] == failed
        assert placeholder.key not in session._decorations
        ledger_write.assert_not_called()
        commit.assert_not_called()

    def test_preserves_an_explicit_terminal_failure_cause(
        self, terminal_failure_case
    ) -> None:
        (
            session,
            context,
            failure_code,
            expected_exception,
            expected_terminal,
            terminal_result,
        ) = terminal_failure_case
        with expected_exception:
            session.finish(failure_code=failure_code)
        assert terminal_result(context.finish.call_args) == expected_terminal
