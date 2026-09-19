"""Projection repair participates in the same maintenance admission as writes."""

import asyncio
import threading

import pytest
from printstash_core.search.passages import SubjectType
from sqlmodel import select

from app.db.models import SearchPassage, SearchReconciliationState
from app.db.session import get_session_factory
from app.runtime import maintenance, search


class TestSearchRuntime:
    def test_defers_search_work_during_foreground_writes(self, db_session):
        assert maintenance.begin_mutating_operation(foreground=True)
        try:
            assert search.process_one(SubjectType.DOCUMENT) == 0
            assert search._index_one() is False
            assert db_session.exec(select(SearchReconciliationState)).all() == []
        finally:
            maintenance.end_mutating_operation(foreground=True)

    def test_defers_burst_work_during_restore(self, db_session):
        maintenance.hold_restore_maintenance()
        try:
            assert search._index_one() is False
        finally:
            maintenance.end_restore_maintenance()

    def test_keeps_burst_inference_disabled_without_ai_consent(
        self, db_session, make_user, make_inference_endpoint
    ):
        from app.db.models import IndexGeneration
        from app.modules.search import configuration, generations
        from app.schemas.inference import SearchSettings
        from app.schemas.search_generations import GenerationProposal

        actor, endpoint = make_user(superuser=True), make_inference_endpoint()
        configuration.update(db_session, SearchSettings(enabled=True))
        pending = generations.prepare(
            db_session,
            actor,
            GenerationProposal(endpoint_id=endpoint.id, index_backend="numpy"),
        )
        configuration.update(db_session, SearchSettings(enabled=False))
        db_session.commit()

        assert search._index_one() is False

        row = db_session.get(IndexGeneration, pending.id, populate_existing=True)
        assert (row.state, row.processed, row.lease_token) == ("building", 0, None)

    @pytest.mark.asyncio
    async def test_waits_for_an_inflight_burst_unit_on_cancellation(self, monkeypatch):
        entered, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def bounded():
            entered.set()
            assert release.wait(timeout=5)
            finished.set()
            return True

        monkeypatch.setattr(search, "process_one", lambda _: 0)
        monkeypatch.setattr(search, "_index_one", bounded)
        task = asyncio.create_task(search.run_search())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_drains_ready_backfill_in_bounded_bursts(self, monkeypatch):
        completed = asyncio.Event()
        loop = asyncio.get_running_loop()
        units = []

        def index_one():
            units.append(True)
            if len(units) == 15:
                loop.call_soon_threadsafe(completed.set)
            return True

        monkeypatch.setattr(search, "process_one", lambda _: 0)
        monkeypatch.setattr(search, "_index_one", index_one, raising=False)
        task = asyncio.create_task(search.run_search())
        try:
            await asyncio.wait_for(completed.wait(), 1.5)
            await asyncio.sleep(0.05)
            assert len(units) == 15
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_stops_a_burst_when_the_index_is_idle(self, monkeypatch):
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        units = []

        def index_one():
            units.append(False)
            loop.call_soon_threadsafe(entered.set)
            return False

        monkeypatch.setattr(search, "process_one", lambda _: 0)
        monkeypatch.setattr(search, "_index_one", index_one, raising=False)
        task = asyncio.create_task(search.run_search())
        try:
            await asyncio.wait_for(entered.wait(), 1.5)
            await asyncio.sleep(0.05)
            assert units == [False]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_defers_projection_repair_during_restore(self, db_session):
        maintenance.hold_restore_maintenance()
        try:
            assert search.process_one(SubjectType.DOCUMENT) == 0
            assert db_session.exec(select(SearchReconciliationState)).all() == []
        finally:
            maintenance.end_restore_maintenance()

    def test_commits_a_projection_repair_partition(self, db_session, make_document):
        document = make_document("Assembly", body="Press the latch")
        assert search.process_one(SubjectType.DOCUMENT) == 1
        with get_session_factory().scoped_session() as session:
            assert (
                session.exec(
                    select(SearchPassage.text).where(
                        SearchPassage.subject_type == "document",
                        SearchPassage.subject_id == document.id,
                    )
                ).one()
                == "Title: Assembly\nBody: Press the latch"
            )

    @pytest.mark.asyncio
    async def test_cancellation_waits_for_projection_repair(self, monkeypatch):
        entered, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def bounded(_kind):
            entered.set()
            assert release.wait(timeout=5)
            finished.set()
            return 1

        monkeypatch.setattr(search, "process_one", bounded)
        task = asyncio.create_task(search.run_search())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class TestPeriodicRepairBudget:
    def test_keeps_periodic_repair_transactions_small(
        self,
        db_session,
        make_model,
        make_search_passage,
    ):
        from printstash_core.search.passages import SearchSubject
        from sqlmodel import select

        from app.db.models import SearchReconciliationState

        ids = []
        for index in range(40):
            model = make_model(f"Stored passage {index}")
            ids.append(model.id)
            make_search_passage(SearchSubject(SubjectType.MODEL, model.id))
        db_session.commit()
        search.process_one(SubjectType.MODEL)
        db_session.expire_all()
        state = db_session.exec(
            select(SearchReconciliationState).where(
                SearchReconciliationState.subject_type == "model"
            )
        ).one()
        assert state.partition_after_id > 0
        assert state.orphan_after_id > 0
        assert sum(id <= state.partition_after_id for id in ids) <= 1
        assert sum(id <= state.orphan_after_id for id in ids) <= 1
