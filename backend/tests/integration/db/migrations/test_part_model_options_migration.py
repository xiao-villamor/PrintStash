"""A 0.13.0 Part Option database preserves legacy choices during upgrade."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "54b21089d3af"
PARENT = "547387477ce4"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestPartModelOptionsMigration:
    def test_upgrade_supports_both_legacy_target_shapes(self, tmp_path: Path) -> None:
        database = tmp_path / "part-model-options.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)
        engine = create_engine(f"sqlite:///{database}")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO models (
                        id, name, slug, hash, next_file_version, created_at, updated_at
                    ) VALUES (
                        1, 'Assembly', 'assembly', :hash, 2,
                        '2026-09-01 00:00:00', '2026-09-01 00:00:00'
                    )
                    """
                ),
                {"hash": "a" * 64},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO files (
                        id, model_id, path, original_filename, file_type, version,
                        size_bytes, sha256, is_recommended, is_external, uploaded_at
                    ) VALUES (
                        1, 1, 'assembly.stl', 'assembly.stl', 'STL', 1,
                        10, :hash, 0, 0, '2026-09-01 00:00:00'
                    )
                    """
                ),
                {"hash": "b" * 64},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_groups (
                        id, model_id, name, name_key, sort_order, created_at
                    ) VALUES (1, 1, 'Body', 'body', 0, '2026-09-01 00:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_options (
                        id, part_group_id, file_id, name, name_key, sort_order,
                        is_default, created_at
                    ) VALUES (
                        1, 1, 1, 'Standard', 'standard', 0, 1,
                        '2026-09-01 00:00:00'
                    )
                    """
                )
            )

        command.upgrade(config, REVISION)

        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("part_options")
        }
        assert columns["file_id"]["nullable"] is True
        assert columns["model_id"]["nullable"] is True
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT file_id, model_id FROM part_options WHERE id = 1")
            ).one() == (1, None)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO models (
                        id, name, slug, hash, next_file_version, created_at, updated_at
                    ) VALUES (
                        2, 'Member', 'member', :hash, 2,
                        '2026-09-01 00:00:00', '2026-09-01 00:00:00'
                    )
                    """
                ),
                {"hash": "c" * 64},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO files (
                        id, model_id, path, original_filename, file_type, version,
                        size_bytes, sha256, is_recommended, is_external, uploaded_at
                    ) VALUES (
                        2, 2, 'member.stl', 'member.stl', 'STL', 1,
                        10, :hash, 0, 0, '2026-09-01 00:00:00'
                    )
                    """
                ),
                {"hash": "d" * 64},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_groups (
                        id, model_id, name, name_key, sort_order, created_at
                    ) VALUES (2, 1, 'Member', 'member', 1, '2026-09-01 00:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_options (
                        id, part_group_id, file_id, model_id, name, name_key,
                        sort_order, is_default, created_at
                    ) VALUES (
                        2, 2, NULL, 2, 'Standard', 'standard', 0, 1,
                        '2026-09-01 00:00:00'
                    )
                    """
                )
            )

        command.downgrade(config, PARENT)

        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("part_options")
        }
        assert columns["file_id"]["nullable"] is False
        assert "model_id" not in columns
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT file_id FROM part_options WHERE id = 2")
                ).scalar_one()
                == 2
            )
        engine.dispose()
