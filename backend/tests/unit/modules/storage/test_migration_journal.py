"""Interrupted journal records must never authorize an implicit activation."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.modules.storage import migration_journal
from app.runtime.maintenance import end_restore_maintenance, restore_in_progress


@pytest.fixture
def journal(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setattr(migration_journal, "journal_path", lambda: path)
    yield path
    end_restore_maintenance()


class TestInspectBeforeWrites:
    def test_empty_journal_allows_normal_startup(self, journal):
        assert migration_journal.events() == []
        assert not migration_journal.inspect_before_writes()

    def test_completed_other_run_cannot_mask_interrupted_copy(self, journal):
        migration_journal.append({"run_id": "one", "nonce": "1", "phase": "baseline"})
        migration_journal.append({"run_id": "two", "nonce": "2", "phase": "complete"})
        assert migration_journal.inspect_before_writes()
        assert restore_in_progress()

    @pytest.mark.parametrize("record", ['{"phase":', '{"phase":"complete"}', "[]"])
    def test_malformed_record_holds_maintenance(self, journal, record):
        journal.write_text(record)
        assert migration_journal.inspect_before_writes()
        assert restore_in_progress()

    def test_completed_activation_with_first_write_is_resolved(self, journal):
        for phase in (
            "activation_intent",
            "database_active",
            "complete",
            "first_write",
        ):
            migration_journal.append({"run_id": "one", "nonce": "1", "phase": phase})
        assert not migration_journal.inspect_before_writes()


class TestAppendJournal:
    def test_parallel_receipts_remain_individually_readable(self, journal):
        entries = [
            {
                "run_id": "copy",
                "nonce": "1",
                "phase": "copy_receipt",
                "object_id": index,
            }
            for index in range(30)
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(migration_journal.append, entries))
        assert (
            sorted(migration_journal.events(), key=lambda item: item["object_id"])
            == entries
        )
