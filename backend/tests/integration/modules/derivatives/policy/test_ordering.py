"""The database orders disable commits and actual producer admission."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import OperationError
from app.db.models import ArtifactDerivative, JobKind
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.modules.derivatives import policy, producers
from tests import factories
from tests.containers import fresh_postgres_database


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def policy_vault(request, tmp_path):
    url = (
        f"sqlite:///{tmp_path / 'policy.sqlite'}"
        if request.param == "sqlite"
        else normalize_database_url(fresh_postgres_database("derivative_policy"))
    )
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if request.param == "sqlite" else {},
    )
    SQLModel.metadata.create_all(engine)
    previous = get_session_factory()
    factory = SQLiteSessionFactory(engine)
    override_session_factory(factory)
    try:
        with Session(engine) as session:
            artifact = factories.build_file(
                session, factories.build_model(session), filename="part.stl"
            )
            factories.build_system_config(session)
            yield factory, artifact.id
    finally:
        override_session_factory(previous)
        engine.dispose()


class TestPolicyOrdering:
    def test_a_disable_commit_precedes_waiting_producer_admission(self, policy_vault):
        factory, file_id = policy_vault
        waiting = Event()
        with factory.scoped_session() as update_session:
            config = policy.lock(update_session)
            config.derivatives_mesh_enabled = False
            update_session.add(config)
            update_session.flush()

            def execute():
                override_session_factory(factory)
                waiting.set()
                with pytest.raises(OperationError, match="derivative_group_disabled"):
                    producers.derive_mesh(file_id)

            with ThreadPoolExecutor(max_workers=1) as threads:
                execution = threads.submit(execute)
                assert waiting.wait(5)
                assert not execution.done()
                update_session.commit()
                execution.result(timeout=20)
        with factory.scoped_session() as session:
            assert session.exec(select(ArtifactDerivative)).all() == []

    def test_admission_committed_before_disable_retains_its_running_rows(
        self, policy_vault
    ):
        factory, file_id = policy_vault
        producers._begin(file_id, JobKind.DERIVATIVES_MESH)
        with factory.scoped_session() as session:
            policy.update(session, {policy.SettingName.MESH: False})
            rows = session.exec(select(ArtifactDerivative)).all()
            assert rows
            assert {row.state.value for row in rows} == {"running"}
            assert {row.attempts for row in rows} == {1}
