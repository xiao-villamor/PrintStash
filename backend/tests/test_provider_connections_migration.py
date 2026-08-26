from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command


def test_fb14_upgrade_and_downgrade_are_structural(tmp_path: Path) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
    )
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{tmp_path / 'provider.sqlite'}"
    )
    command.upgrade(config, "fa13c4e7b9d2")
    command.upgrade(config, "fb14d5e8a7c3")
    from sqlalchemy import create_engine

    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    inspector = inspect(engine)
    assert {
        "provider_connections",
        "provider_oauth_states",
        "browser_pairing_codes",
        "browser_devices",
    } <= set(inspector.get_table_names())
    assert any(
        item["name"] == "uq_provider_connection_user_provider"
        for item in inspector.get_unique_constraints("provider_connections")
    )
    assert any(
        item["name"] == "uq_browser_device_user_name"
        for item in inspector.get_unique_constraints("browser_devices")
    )
    assert "ix_browser_devices_credential_hash" in {
        item["name"] for item in inspector.get_indexes("browser_devices")
    }
    command.downgrade(config, "fa13c4e7b9d2")
    assert not (
        {
            "provider_connections",
            "provider_oauth_states",
            "browser_pairing_codes",
            "browser_devices",
        }
        & set(inspect(engine).get_table_names())
    )
    engine.dispose()
