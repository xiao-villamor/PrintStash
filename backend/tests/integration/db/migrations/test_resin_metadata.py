"""Resin metadata upgrades leave existing FDM artifacts readable."""

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from alembic import command
from app.db.migrate import _alembic_config, run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories import build_file, build_metadata, build_model


class TestResinMetadataMigration:
    @pytest.mark.parametrize(
        "dialect",
        [
            "sqlite",
            pytest.param("postgresql", marks=pytest.mark.postgres),
        ],
    )
    def test_preserves_existing_artifact_metadata(self, tmp_path, dialect):
        url = (
            fresh_postgres_database("resin_upgrade")
            if dialect == "postgresql"
            else f"sqlite:///{tmp_path / 'resin.sqlite'}"
        )
        run_migrations(url)
        engine = create_engine(normalize_database_url(url))
        try:
            with Session(engine) as session:
                artifact = build_file(
                    session, build_model(session), filename="existing.gcode"
                )
                meta = build_metadata(
                    session, artifact, layer_height_mm=0.2, estimated_time_s=1800
                )
                file_id = artifact.id
                assert meta.layer_count is None
            config = _alembic_config(url)
            command.downgrade(config, "105991549e18")
            command.upgrade(config, "b74196f14d23")
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        "SELECT layer_height_mm, estimated_time_s, layer_count, exposure_time_s, resin_volume_ml "
                        "FROM metadata WHERE file_id=:id"
                    ),
                    {"id": file_id},
                ).one()
                assert row == (0.2, 1800, None, None, None)
                connection.execute(
                    text(
                        "UPDATE metadata SET layer_count=120, exposure_time_s=2.5, resin_volume_ml=2.5 WHERE file_id=:id"
                    ),
                    {"id": file_id},
                )
                connection.execute(
                    text("UPDATE files SET file_type='RESIN' WHERE id=:id"),
                    {"id": file_id},
                )
            with Session(engine) as session:
                from app.db.models import File, FileType

                assert session.get(File, file_id).file_type is FileType.RESIN
        finally:
            engine.dispose()
