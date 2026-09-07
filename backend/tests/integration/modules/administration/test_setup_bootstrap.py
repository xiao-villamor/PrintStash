"""Independent setup transactions must agree on the installation's one first owner.

PostgreSQL locks must exclude peers until the transaction ends, then recheck
committed configuration even when a peer has already cached the unconfigured row.
"""

from datetime import datetime, timezone
from typing import Iterator

import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import SystemConfig, User
from app.db.url import normalize_database_url
from app.modules.administration import setup_bootstrap
from tests.containers import postgres_url
from tests.factories import build_system_config
from tests.fakes.setup_process import race_setup_workers


@pytest.fixture
def postgres_setup_engine() -> Iterator[Engine]:
    """Isolate setup rows from other contracts using the shared PostgreSQL server."""
    schema = "setup_bootstrap"
    engine = create_engine(
        normalize_database_url(postgres_url()),
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        try:
            SQLModel.metadata.create_all(engine)
            yield engine
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


class TestLockInstallation:
    @pytest.mark.postgres
    def test_excludes_a_competing_postgres_setup_transaction(
        self, postgres_setup_engine: Engine
    ) -> None:
        with (
            Session(postgres_setup_engine) as first,
            Session(postgres_setup_engine) as peer,
        ):
            setup_bootstrap.lock_installation(first)
            peer.execute(text("SET LOCAL lock_timeout = '100ms'"))

            with pytest.raises(OperationalError) as exc:
                setup_bootstrap.lock_installation(peer)

            assert isinstance(exc.value.orig, LockNotAvailable)

    @pytest.mark.postgres
    def test_rejects_postgres_setup_after_configuration_commits(
        self, postgres_setup_engine: Engine
    ) -> None:
        with (
            Session(postgres_setup_engine) as first,
            Session(postgres_setup_engine) as peer,
        ):
            config = build_system_config(first)
            cached = peer.get(SystemConfig, config.id)
            assert cached is not None
            assert cached.configured_at is None
            setup_bootstrap.lock_installation(first)
            config.configured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            first.commit()

            with pytest.raises(OperationError, match="^already_configured$") as exc:
                setup_bootstrap.lock_installation(peer)

            assert exc.value.kind is ErrorKind.CONFLICT


class TestFirstOwnerConcurrency:
    def test_two_sqlite_api_processes_create_exactly_one_owner(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'first-owner.sqlite'}"
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        try:
            results = race_setup_workers(url, tmp_path)
            assert sorted(code for code, _ in results) == [201, 409], results
            with Session(engine) as session:
                assert (
                    len(session.exec(select(User).where(User.is_superuser)).all()) == 1
                )
        finally:
            engine.dispose()
