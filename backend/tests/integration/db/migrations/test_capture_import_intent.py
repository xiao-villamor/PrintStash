"""New import intent columns preserve earlier pending captures and their files."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.factories.migration_rows import seed_schema_row
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


def test_upgrades_existing_browser_capture_and_validates_intent(tmp_path):
    url = f"sqlite:///{tmp_path / 'capture.sqlite'}"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "005fa96dc55b")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            seed_schema_row(connection, "users", id=5, username="printer")
            seed_schema_row(
                connection,
                "inbox_items",
                id=22,
                owner_user_id=5,
                source_kind="BROWSER",
                state="CAPTURED",
                source_url="https://files.example.com/bracket.stl",
            )
            seed_schema_row(
                connection,
                "capture_upload_slots",
                id="existing-slot",
                inbox_item_id=22,
                source_file_id="bracket",
            )
        command.upgrade(config, "6af95a6c6f24")
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT on_ready, review_reason FROM inbox_items WHERE id = 22")
            ).one() == ("review", None)
            assert (
                connection.execute(
                    text(
                        "SELECT inbox_item_id FROM capture_upload_slots WHERE id = 'existing-slot'"
                    )
                ).scalar_one()
                == 22
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text("UPDATE inbox_items SET on_ready = 'automatic'")
                )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text("UPDATE inbox_items SET review_reason = 'unknown'")
                )
    finally:
        engine.dispose()
