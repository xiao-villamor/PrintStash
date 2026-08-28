"""Deleting a user must take their provider credentials with them.

Provider connection and pairing rows hold OAuth tokens belonging to one user. If
a hard delete leaves them behind, the credentials outlive the account — a data
retention problem, and rows that point at a user id that no longer exists.

The cascade is asserted against a real delete rather than the schema, because a
declared `ondelete` that the ORM overrides on its own relationship is a cascade
that does not happen.

The second row checks the migration is *structural* both ways: an upgrade and a
downgrade that change data are a downgrade nobody can safely run.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect
from sqlmodel import Session, delete, select

from alembic import command
from app.db.models import (
    BrowserDevice,
    BrowserPairingCode,
    CaptureProvider,
    ProviderConnection,
    ProviderOAuthState,
    User,
)
from tests.factories import build_user
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestUser:
    def test_deleting_a_user_cascades_to_every_provider_row(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(
            db_session, username="provider-cascade", password="Password123"
        )
        db_session.add_all(
            [
                ProviderConnection(user_id=user.id, provider=CaptureProvider.CULTS),
                ProviderOAuthState(
                    user_id=user.id,
                    provider=CaptureProvider.MYMINIFACTORY,
                    state_hash="a" * 64,
                    redirect_uri="https://example.test/callback",
                    expires_at=user.created_at,
                ),
                BrowserPairingCode(
                    user_id=user.id, code_hash="b" * 64, expires_at=user.created_at
                ),
                BrowserDevice(
                    user_id=user.id, name="Browser", credential_hash="c" * 64
                ),
            ]
        )
        db_session.commit()
        db_session.exec(delete(User).where(User.id == user.id))
        db_session.commit()
        assert not db_session.exec(select(ProviderConnection)).all()
        assert not db_session.exec(select(ProviderOAuthState)).all()
        assert not db_session.exec(select(BrowserPairingCode)).all()
        assert not db_session.exec(select(BrowserDevice)).all()


class TestDowngrade:
    def test_fb14_leaves_no_trace_after_a_downgrade(self, tmp_path: Path) -> None:
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
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
