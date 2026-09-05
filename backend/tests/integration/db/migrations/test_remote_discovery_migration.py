"""Inventory tables are additive; old source locators and checkpoints survive."""

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestRemoteDiscoveryUpgrade:
    def test_upgrade_preserves_historical_source_checkpoint(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'discovery-upgrade.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "046685afd7ea")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "external_libraries",
                    id=1,
                    name="Remote source",
                    root_path="",
                    source_kind="S3",
                    source_prefix="models",
                    collection_mode="MIRROR",
                    watch_mode="OFF",
                    last_scan_status="OK",
                )
                seed_schema_row(
                    connection,
                    "external_library_checkpoints",
                    id=1,
                    library_id=1,
                    epoch="old-epoch",
                    cursor='[{"directory":"models","after":"a.gcode"}]',
                    observed_keys_json='["models/a.gcode"]',
                    complete=False,
                )
                before = connection.execute(
                    text("SELECT * FROM external_library_checkpoints WHERE id=1")
                ).one()
            command.upgrade(config, "5f0f887bdd0b")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT * FROM external_library_checkpoints WHERE id=1")
                    ).one()
                    == before
                )
                assert (
                    connection.execute(
                        text("SELECT source_prefix FROM external_libraries WHERE id=1")
                    ).scalar_one()
                    == "models"
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM remote_discovery_inventories")
                    ).scalar_one()
                    == 0
                )
            command.downgrade(config, "046685afd7ea")
            command.upgrade(config, "5f0f887bdd0b")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT * FROM external_library_checkpoints WHERE id=1")
                    ).one()
                    == before
                )
                assert (
                    "remote_discovery_entries" in inspect(connection).get_table_names()
                )
        finally:
            engine.dispose()


class TestPostgresDiscoveryUpgrade:
    @pytest.mark.postgres
    def test_postgres_upgrade_runs_durable_inventory_queries(self, monkeypatch):
        from contextlib import contextmanager
        from types import SimpleNamespace

        from app.db.migrate import run_migrations
        from app.db.session import SQLiteSessionFactory
        from app.db.url import normalize_database_url
        from app.services import remote_discovery
        from app.services.library_source import RemoteLibrarySource
        from app.services.remote_io import RemoteEntry
        from tests.containers import postgres_url
        from tests.factories.migration_rows import (
            RELEASED_V0121_REVISION,
            create_released_v0121_postgres_schema,
        )

        url = postgres_url()
        engine = create_engine(normalize_database_url(url))
        config = _alembic_config(url)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP SCHEMA public CASCADE")
                connection.exec_driver_sql("CREATE SCHEMA public")
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
            command.upgrade(config, "046685afd7ea")
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "external_libraries",
                    id=1,
                    name="PG source",
                    root_path="",
                    source_kind="S3",
                    source_prefix="models",
                    collection_mode="MIRROR",
                    watch_mode="OFF",
                    last_scan_status="OK",
                )
                seed_schema_row(
                    connection,
                    "external_library_checkpoints",
                    id=1,
                    library_id=1,
                    epoch="retained",
                    observed_keys_json='["models/old.gcode"]',
                    complete=False,
                )
            command.upgrade(config, "5f0f887bdd0b")
            command.check(config)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT epoch FROM external_library_checkpoints WHERE id=1"
                        )
                    ).scalar_one()
                    == "retained"
                )
            factory = SQLiteSessionFactory(engine)
            monkeypatch.setattr(
                remote_discovery, "get_session_factory", lambda: factory
            )
            calls = []

            @contextmanager
            def listing(directory):
                calls.append(directory)
                yield (
                    RemoteEntry(f"models/{index}.gcode", 6, False)
                    for index in range(1001)
                )

            backend = SimpleNamespace(
                backend_name="postgres-inventory-test", iter_directory=listing
            )
            first = RemoteLibrarySource(backend).list_page(
                "models", cursor=None, limit=1000
            )
            second = RemoteLibrarySource(backend).list_page(
                "models", cursor=first.next_cursor, limit=1000
            )
            assert len(first.entries) == 1000
            assert [entry.key for entry in second.entries] == ["models/1000.gcode"]
            assert second.complete is True
            assert calls == ["models"]
            remote_discovery.retire_inventory(second.inventory_id)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM remote_discovery_entries")
                    ).scalar_one()
                    == 0
                )
            command.downgrade(config, "046685afd7ea")
            command.upgrade(config, "5f0f887bdd0b")
            command.check(config)
        finally:
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP SCHEMA public CASCADE")
                connection.exec_driver_sql("CREATE SCHEMA public")
            run_migrations(url)
            engine.dispose()
