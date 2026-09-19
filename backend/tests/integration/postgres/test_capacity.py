"""PostgreSQL admission serializes competing operation budgets."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlmodel import create_engine

from app.core.errors import OperationError
from app.db.migrate import run_migrations
from app.db.session import SQLiteSessionFactory
from app.db.url import normalize_database_url
from app.modules.storage.capacity import CapacityManager, CapacityResource
from tests.containers import postgres_url


class TestPostgresCapacity:
    def test_serializes_admission(self):
        url = postgres_url()
        run_migrations(url)
        engine = create_engine(normalize_database_url(url))
        manager = CapacityManager(SQLiteSessionFactory(engine), headroom_bytes=0)
        domain = uuid4().hex
        ids = [f"capacity-test:{uuid4().hex}" for _ in range(2)]
        barrier = Barrier(2)

        try:

            def reserve(owner):
                barrier.wait()
                try:
                    manager.reserve(
                        owner,
                        [CapacityResource.for_quota(domain, 60, 100, role="test")],
                    )
                    return True
                except OperationError:
                    return False

            with ThreadPoolExecutor(2) as pool:
                outcomes = list(pool.map(reserve, ids))
            assert sorted(outcomes) == [False, True]
            assert manager.reserved_bytes()[f"quota:{domain}"] == 60
        finally:
            for owner in ids:
                manager.release(owner)
            engine.dispose()

    def test_upgrade_preserves_models(self):
        from alembic.config import Config
        from sqlalchemy import inspect, text
        from sqlalchemy.engine import make_url

        from alembic import command
        from tests.factories.migration_rows import (
            RELEASED_V0121_REVISION,
            create_released_v0121_postgres_schema,
            seed_schema_row,
        )
        from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

        url = normalize_database_url(postgres_url())
        database = f"capacity_upgrade_{uuid4().hex}"
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        isolated_url = (
            make_url(url).set(database=database).render_as_string(hide_password=False)
        )
        engine = create_engine(isolated_url)
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", isolated_url)
        try:
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
            command.upgrade(config, "0a6b1f868ae0")
            identity = 1
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "models",
                    id=identity,
                    name="Preserved PostgreSQL model",
                    slug="preserved",
                    hash="a" * 64,
                )
            command.upgrade(config, "head")
            assert "capacity_reservations" in inspect(engine).get_table_names()
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT name FROM models WHERE id=:id"), {"id": identity}
                    ).scalar_one()
                    == "Preserved PostgreSQL model"
                )
        finally:
            engine.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
            admin.dispose()
