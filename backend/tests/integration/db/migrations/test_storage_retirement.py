"""Retirement upgrades preserve exact receipts and allow fresh canonical generations."""

from contextlib import redirect_stdout
from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    create_pre_storage_retirement_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", marks=pytest.mark.postgres, id="postgresql"),
    ]
)
def retirement_database(request, tmp_path):
    url = (
        f"sqlite:///{tmp_path / 'ownership.sqlite'}"
        if request.param == "sqlite"
        else normalize_database_url(fresh_postgres_database("retirement_upgrade"))
    )
    engine = create_engine(url)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    with engine.begin() as connection:
        create_pre_storage_retirement_schema(connection)
        seed_schema_row(
            connection,
            "owned_storage_objects",
            id=7,
            backend="local",
            namespace="vault:/existing",
            key="existing.stl",
            provider_ref=None,
            object_kind="artifact",
            state="committed",
            token="historical-token",
            size_bytes=12,
            sha256="a" * 64,
            device=10,
            inode=20,
            ctime_ns=30,
        )
        seed_schema_row(
            connection,
            "owned_storage_objects",
            id=8,
            backend="s3",
            namespace="bucket/root",
            key="pending.stl",
            provider_ref="b" * 64,
            object_kind="artifact",
            state="pending",
            token=None,
            size_bytes=42,
            sha256="c" * 64,
        )
    command.stamp(config, "809afb73e776")
    yield engine, config
    engine.dispose()


class TestStorageRetirementUpgrade:
    def test_preserves_populated_receipts_through_upgrade_roundtrip(
        self, retirement_database
    ):
        engine, config = retirement_database

        command.upgrade(config, "8ce8989462c0")
        command.downgrade(config, "809afb73e776")
        command.upgrade(config, "8ce8989462c0")

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, state, token, size_bytes, sha256, device, inode, ctime_ns, provider_ref, publication_generation FROM owned_storage_objects ORDER BY id"
                )
            ).all() == [
                (
                    7,
                    "committed",
                    "historical-token",
                    12,
                    "a" * 64,
                    10,
                    20,
                    30,
                    None,
                    "legacy-7",
                ),
                (
                    8,
                    "pending",
                    None,
                    42,
                    "c" * 64,
                    None,
                    None,
                    None,
                    "b" * 64,
                    "legacy-8",
                ),
            ]
        assert "storage_publication_locators" in inspect(engine).get_table_names()

    def test_retired_history_allows_one_new_active_generation(
        self, retirement_database
    ):
        engine, config = retirement_database
        command.upgrade(config, "8ce8989462c0")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE owned_storage_objects SET state='retiring' WHERE id=7")
            )
            seed_schema_row(
                connection,
                "owned_storage_objects",
                id=9,
                backend="local",
                namespace="vault:/existing",
                key="existing.stl",
                provider_ref=None,
                object_kind="artifact",
                state="pending",
                publication_generation="new-generation",
                token=None,
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                seed_schema_row(
                    connection,
                    "owned_storage_objects",
                    id=10,
                    backend="local",
                    namespace="vault:/existing",
                    key="existing.stl",
                    provider_ref=None,
                    object_kind="artifact",
                    state="pending",
                    publication_generation="third-generation",
                    token=None,
                )

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id,state,token FROM owned_storage_objects WHERE key='existing.stl' ORDER BY id"
                )
            ).all() == [(7, "retiring", "historical-token"), (9, "pending", None)]

    @pytest.mark.parametrize(
        "column,value",
        [("state", "invented"), ("publication_generation", "")],
        ids=["unknown-state", "empty-generation"],
    )
    def test_rejects_invalid_publication_authority(
        self, retirement_database, column, value
    ):
        engine, config = retirement_database
        command.upgrade(config, "8ce8989462c0")

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        f"UPDATE owned_storage_objects SET {column}=:value WHERE id=7"
                    ),
                    {"value": value},
                )

    def test_downgrade_refuses_to_erase_retirement_authority(self, retirement_database):
        engine, config = retirement_database
        command.upgrade(config, "8ce8989462c0")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE owned_storage_objects SET state='retiring' WHERE id=7")
            )

        with pytest.raises(RuntimeError, match="retirement_history_prevents_downgrade"):
            command.downgrade(config, "809afb73e776")

        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT token,publication_generation FROM owned_storage_objects WHERE id=7"
                )
            ).one() == ("historical-token", "legacy-7")

    def test_offline_postgres_render_preserves_retirement_constraints(self):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://unused/unused")
        output = StringIO()

        with redirect_stdout(output):
            command.upgrade(config, "809afb73e776:8ce8989462c0", sql=True)

        ddl = output.getvalue()
        assert "CREATE TABLE storage_publication_locators" in ddl
        assert (
            "UPDATE owned_storage_objects SET publication_generation = 'legacy-'" in ddl
        )
        assert "state IN ('pending', 'committed', 'blocked', 'retiring')" in ddl
        assert "WHERE provider_ref IS NOT NULL AND state != 'retiring'" in ddl
