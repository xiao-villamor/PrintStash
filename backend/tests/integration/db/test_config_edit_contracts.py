"""Persisted vault configuration versions follow edits, not operational progress."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text, update
from sqlmodel import Session, create_engine

from app.db.migrate import run_migrations
from app.db.models import SystemConfig
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_system_config


@pytest.fixture(scope="module")
def pg_config_engine() -> Iterator[Engine]:
    url = fresh_postgres_database("config_edit_contracts")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def config_db(request: pytest.FixtureRequest, db_session: Session) -> Iterator[Engine]:
    engine = (
        request.getfixturevalue("pg_config_engine")
        if request.param == "postgresql"
        else db_session.get_bind()
    )
    with Session(engine) as session:
        build_system_config(session, currency="USD", oidc_client_id="initial")
    try:
        yield engine
    finally:
        if request.param == "postgresql":
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM system_config"))


def _version(session: Session) -> int:
    return session.execute(
        text("SELECT vault_edit_version FROM system_config WHERE id=1")
    ).scalar_one()


class TestConfigurationEditVersion:
    def test_new_configuration_starts_with_a_positive_version(
        self, config_db: Engine
    ) -> None:
        with Session(config_db) as session:
            assert _version(session) == 1

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("currency", "EUR", id="currency"),
            pytest.param("data_dir", "/example/data", id="data-root"),
            pytest.param("thumb_dir", "/example/thumbs", id="thumb-root"),
            pytest.param("storage_backend", "s3", id="storage-backend"),
            pytest.param("storage_provider", "webdav", id="storage-provider"),
            pytest.param(
                "storage_provider_config_json",
                '{"root":"changed"}',
                id="provider-config",
            ),
            pytest.param(
                "storage_provider_secret_json",
                '{"password":"test-only"}',
                id="provider-secret",
            ),
            pytest.param("s3_root", "changed", id="legacy-s3-root"),
            *[
                pytest.param(field, "changed", id=field)
                for field in (
                    "s3_bucket",
                    "s3_endpoint_url",
                    "s3_region",
                    "s3_access_key",
                    "s3_secret_key",
                    "backup_s3_bucket",
                    "backup_s3_endpoint_url",
                    "backup_s3_region",
                    "backup_s3_access_key",
                    "backup_s3_secret_key",
                    "oidc_issuer_url",
                    "oidc_client_id",
                    "oidc_client_secret",
                    "oidc_scopes",
                    "oidc_username_claim",
                    "oidc_groups_claim",
                    "oidc_admin_groups",
                    "oidc_display_name",
                    "oidc_redirect_uri",
                )
            ],
            *[
                pytest.param(field, False, id=field)
                for field in (
                    "auto_mark_known_good",
                    "manual_local_backup_enabled",
                    "automatic_local_backup_enabled",
                    "derivatives_mesh_enabled",
                    "derivatives_gcode_enabled",
                    "derivatives_toolpath_enabled",
                )
            ],
            *[
                pytest.param(field, True, id=field)
                for field in (
                    "external_libraries_enabled",
                    "automatic_backups_enabled",
                    "oidc_enabled",
                    "oidc_allow_insecure_http",
                )
            ],
            *[
                pytest.param(field, 7, id=field)
                for field in (
                    "backup_retention_days",
                    "trash_retention_days",
                    "storage_min_free_bytes",
                )
            ],
            pytest.param("automatic_backup_time_utc", "04:10", id="backup-time"),
            pytest.param("model_thumbnail_width", 1280, id="thumbnail-width"),
        ],
    )
    def test_legacy_edits_advance_the_configuration_version(
        self, config_db: Engine, field: str, value: object
    ) -> None:
        with Session(config_db) as session:
            before = _version(session)
            session.execute(
                update(SystemConfig).where(SystemConfig.id == 1).values({field: value})
            )
            session.commit()
        with Session(config_db) as session:
            assert _version(session) > before

    def test_operational_bookkeeping_preserves_the_editing_base(
        self, config_db: Engine
    ) -> None:
        with Session(config_db) as session:
            before = _version(session)
            session.execute(
                text(
                    "UPDATE system_config SET automatic_backup_last_attempt_at = CURRENT_TIMESTAMP, "
                    "configured_at = CURRENT_TIMESTAMP, notifications_enabled = true WHERE id=1"
                )
            )
            session.commit()
        with Session(config_db) as session:
            assert _version(session) == before

    def test_rollback_preserves_the_previous_editing_base(
        self, config_db: Engine
    ) -> None:
        with Session(config_db) as session:
            before = _version(session)
            session.execute(text("UPDATE system_config SET currency='EUR' WHERE id=1"))
            assert _version(session) > before
            session.rollback()
        with Session(config_db) as session:
            assert _version(session) == before
            assert (
                session.execute(
                    text("SELECT currency FROM system_config WHERE id=1")
                ).scalar_one()
                == "USD"
            )

    def test_an_explicit_version_advance_is_not_counted_twice(
        self, config_db: Engine
    ) -> None:
        with Session(config_db) as session:
            before = _version(session)
            session.execute(
                text(
                    "UPDATE system_config SET currency='EUR', vault_edit_version=vault_edit_version+1 WHERE id=1"
                )
            )
            session.commit()
        with Session(config_db) as session:
            assert _version(session) == before + 1

    def test_no_op_writes_preserve_the_editing_base(self, config_db: Engine) -> None:
        with Session(config_db) as session:
            before = _version(session)
            session.execute(text("UPDATE system_config SET currency='USD' WHERE id=1"))
            session.commit()
        with Session(config_db) as session:
            assert _version(session) == before
