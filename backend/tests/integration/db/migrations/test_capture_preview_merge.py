"""Either installed branch upgrades to one head without losing its saved state."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def merge_url(request, tmp_path):
    if request.param == "postgresql":
        from app.db.url import normalize_database_url
        from tests.containers import fresh_postgres_database

        return normalize_database_url(fresh_postgres_database("capture_preview_merge"))
    return f"sqlite:///{tmp_path / 'merge.sqlite'}"


class TestCapturePreviewMerge:
    @pytest.mark.parametrize(
        "previous",
        [
            pytest.param("14982d207a3e", id="gallery-installed"),
            pytest.param("3c9a19d6deb9", id="preview-settings-installed"),
        ],
    )
    def test_preserves_installed_branch_data(self, merge_url, previous):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", merge_url.replace("%", "%%"))
        engine = create_engine(merge_url)
        try:
            if engine.dialect.name == "postgresql":
                from tests.factories.migration_rows import (
                    RELEASED_V0121_REVISION,
                    create_released_v0121_postgres_schema,
                )

                with engine.begin() as connection:
                    create_released_v0121_postgres_schema(connection)
                command.stamp(config, RELEASED_V0121_REVISION)
            command.upgrade(config, previous)
            with engine.begin() as connection:
                seed_schema_row(connection, "system_config", id=1, currency="EUR")
                seed_schema_row(
                    connection,
                    "models",
                    id=7,
                    name="Saved",
                    slug="saved",
                    hash="a" * 64,
                )
                seed_schema_row(
                    connection,
                    "model_provenance_sources",
                    id=8,
                    model_id=7,
                    provider="thingiverse",
                    canonical_url="https://www.thingiverse.com/thing:1",
                    identity_key="b" * 64,
                )
                seed_schema_row(
                    connection,
                    "model_source_covers",
                    id=9,
                    provenance_source_id=8,
                    storage_key="covers/saved.webp",
                    content_type="image/webp",
                    size_bytes=20,
                    **({"position": 1} if previous == "14982d207a3e" else {}),
                )
                if previous == "3c9a19d6deb9":
                    connection.execute(
                        text(
                            "UPDATE system_config SET derivatives_previews_include_poles=true"
                        )
                    )
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars().all() == ["005fa96dc55b"]
                assert connection.execute(
                    text(
                        "SELECT storage_key, position FROM model_source_covers WHERE id=9"
                    )
                ).one() == ("covers/saved.webp", 1 if previous == "14982d207a3e" else 0)
                currency, poles, version = connection.execute(
                    text(
                        "SELECT currency, derivatives_previews_include_poles, vault_edit_version FROM system_config WHERE id=1"
                    )
                ).one()
                assert currency == "EUR"
                assert poles == (True if previous == "3c9a19d6deb9" else None)
                connection.execute(
                    text("UPDATE system_config SET currency='GBP' WHERE id=1")
                )
                assert (
                    connection.execute(
                        text("SELECT vault_edit_version FROM system_config WHERE id=1")
                    ).scalar_one()
                    == version + 1
                )
                assert "gallery_json" in {
                    column["name"]
                    for column in inspect(connection).get_columns("inbox_items")
                }
        finally:
            engine.dispose()
