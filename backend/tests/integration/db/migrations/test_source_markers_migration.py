"""Nullable source markers preserve existing linked Artifact identities."""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestSourceMarkersUpgrade:
    @pytest.mark.parametrize("mtime", [None, 0.0, 1_750_000_000.0])
    def test_preserves_linked_artifacts_without_backfilling_markers(
        self, tmp_path: Path, mtime: float | None
    ) -> None:
        url = f"sqlite:///{tmp_path / 'source-markers.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0aece77e15cf")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                    INSERT INTO models (id, name, slug, hash, next_file_version, created_at, updated_at)
                    VALUES (1, 'Linked', 'linked', :hash, 2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                    {"hash": "a" * 64},
                )
                connection.execute(
                    text("""
                    INSERT INTO files (id, model_id, path, original_filename, file_type, version,
                        size_bytes, sha256, is_recommended, is_external, source_key, source_mtime,
                        source_verified_at, uploaded_at)
                    VALUES (1, 1, 'source://1/models/part.gcode', 'part.gcode', 'GCODE', 1,
                        6, :hash, 0, 1, 'models/part.gcode', :mtime, '2026-09-01 00:00:00', CURRENT_TIMESTAMP)
                """),
                    {"hash": "b" * 64, "mtime": mtime},
                )
            command.upgrade(config, "046685afd7ea")
            with engine.begin() as connection:
                row = connection.execute(
                    text("""
                    SELECT path, source_key, sha256, source_mtime, source_etag, source_version_id FROM files WHERE id=1
                """)
                ).one()
                assert row == (
                    "source://1/models/part.gcode",
                    "models/part.gcode",
                    "b" * 64,
                    mtime,
                    None,
                    None,
                )
                connection.execute(
                    text(
                        "UPDATE files SET source_etag='tag', source_version_id='v1' WHERE id=1"
                    )
                )
            command.downgrade(config, "0aece77e15cf")
            command.upgrade(config, "046685afd7ea")
            with engine.connect() as connection:
                preserved = connection.execute(
                    text(
                        "SELECT source_key, source_mtime, sha256, source_etag, source_version_id FROM files WHERE id=1"
                    )
                ).one()
                assert preserved == ("models/part.gcode", mtime, "b" * 64, None, None)
        finally:
            engine.dispose()
