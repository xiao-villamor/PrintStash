"""Configuration patches are one transaction on the supported PostgreSQL backend."""

from collections.abc import Iterator

import pytest
from fastapi import Response
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine

from app.api.v1.config import VaultConfigUpdate, update_config
from app.core.config import settings
from app.db.migrate import run_migrations
from app.db.models import SystemConfig
from app.db.url import normalize_database_url
from app.schemas.editing import EditPrecondition
from tests.containers import fresh_postgres_database
from tests.factories import build_system_config, build_user


@pytest.fixture
def pg_config() -> Iterator[Engine]:
    url = fresh_postgres_database("config_transactions")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        with Session(engine) as session:
            build_system_config(
                session, oidc_client_id="original", backup_retention_days=14
            )
        yield engine
    finally:
        engine.dispose()


class TestConfigurationTransaction:
    def test_rejects_the_whole_patch_after_a_failed_final_write(
        self, pg_config: Engine
    ) -> None:
        original_client_id = settings.oidc_client_id
        original_retention = settings.backup_retention_days
        with pg_config.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE system_config ADD CONSTRAINT reject_config_tail "
                "CHECK (oidc_client_id IS DISTINCT FROM 'rejected-by-database')"
            )
        with Session(pg_config) as session:
            actor = build_user(session, superuser=True)
            with pytest.raises(IntegrityError, match="reject_config_tail"):
                update_config(
                    VaultConfigUpdate(
                        derivatives_mesh_enabled=False,
                        auto_mark_known_good=False,
                        external_libraries_enabled=True,
                        automatic_backups_enabled=True,
                        automatic_backup_time_utc="03:15",
                        currency="EUR",
                        backup_retention_days=7,
                        oidc_client_id="rejected-by-database",
                    ),
                    session=session,
                    response=Response(),
                    actor=actor,
                    precondition=EditPrecondition(),
                )
        with Session(pg_config) as session:
            row = session.get(SystemConfig, 1)
            assert row is not None
            assert row.derivatives_mesh_enabled is None
            assert row.auto_mark_known_good is True
            assert row.external_libraries_enabled is False
            assert row.automatic_backups_enabled is False
            assert row.automatic_backup_time_utc == "02:00"
            assert row.currency is None
            assert row.backup_retention_days == 14
            assert row.oidc_client_id == "original"
        assert settings.oidc_client_id == original_client_id
        assert settings.backup_retention_days == original_retention

    def test_persists_the_accepted_mixed_patch(self, pg_config: Engine) -> None:
        with Session(pg_config) as session:
            actor = build_user(session, superuser=True)
            receipt = update_config(
                VaultConfigUpdate(
                    derivatives_mesh_enabled=False,
                    auto_mark_known_good=False,
                    external_libraries_enabled=True,
                    automatic_backups_enabled=True,
                    automatic_backup_time_utc="03:15",
                    currency="EUR",
                    backup_retention_days=7,
                    oidc_client_id="accepted-client",
                ),
                session=session,
                response=Response(),
                actor=actor,
                precondition=EditPrecondition(),
            )
        with Session(pg_config) as session:
            row = session.get(SystemConfig, 1)
            assert row is not None
            for name in (
                "derivatives_mesh_enabled",
                "auto_mark_known_good",
                "external_libraries_enabled",
                "automatic_backups_enabled",
                "automatic_backup_time_utc",
                "currency",
                "backup_retention_days",
                "oidc_client_id",
            ):
                assert getattr(row, name) == getattr(receipt, name)
            assert row.currency == "EUR"
            assert row.oidc_client_id == "accepted-client"
