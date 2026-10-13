"""Both engines honor live policy around real producer execution."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from hashlib import sha256
from threading import Event

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from app.db import session as db_session_module
from app.db.models import JobKind, JobState
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.modules.derivatives import jobs as derivative_jobs
from app.modules.derivatives import policy, records
from app.modules.derivatives.source import subject_key
from app.modules.storage.storage_backend.runtime import bind_backend, get_backend
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog
from app.modules.work.jobs import jobs
from app.modules.work.submission import submit
from tests import factories
from tests.contract.modules.work._harness import (
    DbosHarness,
    InlineHarness,
    shared_app_db,
)
from tests.factories import content
from tests.fakes.derivative_storage import GatedDerivativeStorage


class TestDerivativeAdmission:
    @pytest.mark.parametrize("engine_kind", ["inline", "dbos"])
    def test_disable_orders_real_producer_attempts(
        self, engine_kind, tmp_path, monkeypatch
    ):
        admitted, release = Event(), Event()
        engine = create_engine(
            f"sqlite:///{tmp_path / 'vault.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", db_session_module._set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        # The SessionFactory boundary supplies the same isolated vault to DBOS threads.
        monkeypatch.setattr(
            db_session_module, "_default_factory", SQLiteSessionFactory(engine)
        )
        monkeypatch.setattr(db_session_module, "_engine", engine)
        monkeypatch.setattr(
            db_session_module,
            "_factory_ctx",
            ContextVar(
                "policy_contract_factory", default=db_session_module._default_factory
            ),
        )
        with shared_app_db():
            factory = get_session_factory()
            catalog = WorkCatalog(derivative_jobs.definitions())
            harness = (
                InlineHarness(catalog)
                if engine_kind == "inline"
                else DbosHarness(catalog, f"sqlite:///{tmp_path / 'engine.sqlite'}")
            )
            catalog_module.bind(harness.engine, catalog)
            original = get_backend()
            try:
                with factory.scoped_session() as session:
                    factories.build_system_config(session)
                    active = factories.build_file(
                        session,
                        factories.build_model(session),
                        filename="active.stl",
                        size_bytes=len(content.binary_stl()),
                        sha256=sha256(content.binary_stl()).hexdigest(),
                    )
                    queued = factories.build_file(
                        session,
                        factories.build_model(session),
                        filename="queued.stl",
                        size_bytes=len(content.binary_stl()),
                        sha256=sha256(content.binary_stl()).hexdigest(),
                    )
                    original.write_bytes(content.binary_stl(), active.path)
                    original.write_bytes(content.binary_stl(), queued.path)
                    active_id, queued_id, active_key = active.id, queued.id, active.path
                bind_backend(
                    GatedDerivativeStorage(
                        held_key=active_key, admitted=admitted, release=release
                    )
                )
                first = jobs.create(
                    definition=JobKind.DERIVATIVES_MESH,
                    subject_key=subject_key(active_id),
                    owner_user_id=None,
                )
                second = jobs.create(
                    definition=JobKind.DERIVATIVES_MESH,
                    subject_key=subject_key(queued_id),
                    owner_user_id=None,
                )
                submit(first)
                submit(second)

                def settle():
                    override_session_factory(factory)
                    harness.settle()

                with ThreadPoolExecutor(max_workers=1) as threads:
                    running = threads.submit(settle)
                    assert admitted.wait(20), (
                        f"producer did not reach storage: {jobs.get(first)}; {harness.engine.active()}"
                    )
                    with factory.scoped_session() as session:
                        policy.update(session, {policy.SettingName.MESH: False})
                    release.set()
                    running.result(timeout=40)
                assert jobs.get(first).state is JobState.COMPLETED
                assert jobs.get(second).state is JobState.CANCELLED
                assert jobs.get(second).error == "derivative_group_disabled"
                with factory.scoped_session() as session:
                    active = session.get(type(active), active_id)
                    queued = session.get(type(queued), queued_id)
                    assert {
                        row.state.value
                        for row in records.rows_for(session, active).values()
                    } == {"ready"}
                    assert records.rows_for(session, queued) == {}
            finally:
                release.set()
                harness.close()
                bind_backend(original)
