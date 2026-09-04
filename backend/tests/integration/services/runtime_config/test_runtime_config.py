"""Core runtime configuration integration tests.

These tests defend singleton setup state, JWT safety, feature toggles, and the
effective configuration response used by the settings UI.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.config import DEFAULT_JWT_SECRET, _overlay, settings
from app.services import runtime_config
from tests.factories import build_user


@pytest.fixture(autouse=True)
def _clean_overlay():
    saved = dict(_overlay)
    yield
    _overlay.clear()
    _overlay.update(saved)


class TestGetOrCreate:
    def test_get_or_create_creates_singleton_row_once(
        self, db_session: Session
    ) -> None:
        first = runtime_config.get_or_create(db_session)

        assert first.id == 1
        second = runtime_config.get_or_create(db_session)
        assert second.id == first.id

    def test_get_or_create_defers_persistence_when_commit_is_disabled(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session, commit=False)

        assert config.id == 1
        assert config in db_session.new

    def test_get_config_returns_the_singleton(self, db_session: Session) -> None:
        config = runtime_config.get_config(db_session)

        assert config.id == 1


class TestIsConfigured:
    def test_is_configured_false_without_configured_at_or_users(
        self, db_session: Session
    ) -> None:
        assert runtime_config.is_configured(db_session) is False

    def test_is_configured_false_when_configured_but_no_users(
        self, db_session: Session
    ) -> None:
        from app.core.time import utcnow

        config = runtime_config.get_or_create(db_session)
        config.configured_at = utcnow()
        db_session.add(config)
        db_session.commit()

        assert runtime_config.is_configured(db_session) is False

    def test_reports_configured_when_setup_finished_with_a_user_present(
        self, db_session: Session
    ) -> None:
        from app.core.time import utcnow

        config = runtime_config.get_or_create(db_session)
        config.configured_at = utcnow()
        db_session.add(config)
        build_user(db_session, "setup-admin", superuser=True)
        db_session.commit()

        assert runtime_config.is_configured(db_session) is True


class TestEnsureJwtSecret:
    def test_ensure_jwt_secret_noop_when_env_var_is_set(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings._frozen, "jwt_secret", "operator-secret")  # noqa: SLF001

        runtime_config.ensure_jwt_secret(db_session)

        assert "jwt_secret" not in _overlay

    def test_ensure_jwt_secret_reuses_persisted_secret(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings._frozen, "jwt_secret", DEFAULT_JWT_SECRET)  # noqa: SLF001
        config = runtime_config.get_or_create(db_session)
        config.jwt_secret = "already-persisted-secret"
        db_session.add(config)
        db_session.commit()

        runtime_config.ensure_jwt_secret(db_session)

        assert _overlay["jwt_secret"] == "already-persisted-secret"

    def test_ensure_jwt_secret_persists_the_secret_it_generates(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings._frozen, "jwt_secret", DEFAULT_JWT_SECRET)  # noqa: SLF001

        runtime_config.ensure_jwt_secret(db_session)

        generated = _overlay["jwt_secret"]
        assert generated and generated != DEFAULT_JWT_SECRET
        stored = runtime_config.get_or_create(db_session)
        assert stored.jwt_secret == generated


class TestSpoolmanConfig:
    def test_a_partial_spoolman_update_keeps_the_stored_api_key(
        self, db_session: Session
    ) -> None:
        runtime_config.set_spoolman_config(
            db_session, base_url="http://spoolman.local:7912/", api_key="fake-key"
        )

        got = runtime_config.spoolman_config(db_session)
        assert got == {
            "base_url": "http://spoolman.local:7912",
            "api_key": "fake-key",
        }

        runtime_config.set_spoolman_config(
            db_session, base_url="http://spoolman.local:9999"
        )

        got2 = runtime_config.spoolman_config(db_session)
        assert got2["api_key"] == "fake-key"
        assert got2["base_url"] == "http://spoolman.local:9999"

    def test_spoolman_config_defaults_when_no_row(self, db_session: Session) -> None:
        assert runtime_config.spoolman_config(db_session) == {
            "base_url": None,
            "api_key": None,
        }

    def test_setting_only_the_api_key_leaves_the_base_url_unset(
        self, db_session: Session
    ) -> None:
        runtime_config.set_spoolman_config(db_session, api_key="fake-api-key")

        assert runtime_config.spoolman_config(db_session) == {
            "base_url": None,
            "api_key": "fake-api-key",
        }


class TestCurrency:
    def test_currency_defaults_to_usd(self, db_session: Session) -> None:
        assert runtime_config.currency(db_session) == "USD"

    def test_currency_round_trips_uppercased(self, db_session: Session) -> None:
        runtime_config.set_currency(db_session, "eur")

        assert runtime_config.currency(db_session) == "EUR"


class TestMarkConfigured:
    def test_mark_configured_is_idempotent(self, db_session: Session) -> None:
        first = runtime_config.mark_configured(db_session)
        assert first.configured_at is not None
        stamp = first.configured_at

        second = runtime_config.mark_configured(db_session)

        assert second.configured_at == stamp

    def test_mark_configured_can_defer_the_commit(self, db_session: Session) -> None:
        config = runtime_config.mark_configured(db_session, commit=False)

        assert config.configured_at is not None
        assert config in db_session.new or config in db_session.dirty


class TestGetEffectiveConfig:
    def test_get_effective_config_masks_secrets_but_reports_presence(
        self, db_session: Session
    ) -> None:
        runtime_config.update_config(
            db_session,
            s3_access_key="AKIAABCDEFGHIJKLMNOP",
            s3_secret_key="fake-secret",
            oidc_client_secret="fake-oidc-secret",
        )

        effective = runtime_config.get_effective_config(db_session)

        assert effective["has_s3_access_key"] is True
        assert "AKIAABCDEFGHIJKLMNOP" not in effective["s3_access_key"]
        assert effective["s3_access_key"].startswith("AKIA")
        assert effective["has_oidc_client_secret"] is True
        assert effective["oidc_enabled"] is False


_BOOLEAN_TOGGLES = [
    ("auto_mark_known_good_enabled", "set_auto_mark_known_good", True),
    ("external_libraries_enabled", "set_external_libraries_enabled", False),
    ("notifications_enabled", "set_notifications_enabled", False),
    ("spoolman_enabled", "set_spoolman_enabled", False),
    ("spoolman_write_enabled", "set_spoolman_write_enabled", False),
    ("spoolman_write_force", "set_spoolman_write_force", False),
]
_TOGGLE_IDS = [getter for getter, _setter, _default in _BOOLEAN_TOGGLES]


class TestBooleanToggles:
    @pytest.mark.parametrize(
        ("getter", "setter", "default"), _BOOLEAN_TOGGLES, ids=_TOGGLE_IDS
    )
    def test_a_boolean_toggle_starts_at_its_shipped_default(
        self, db_session: Session, getter: str, setter: str, default: bool
    ) -> None:
        assert getattr(runtime_config, getter)(db_session) is default

    @pytest.mark.parametrize(
        ("getter", "setter", "default"), _BOOLEAN_TOGGLES, ids=_TOGGLE_IDS
    )
    def test_a_boolean_toggle_round_trips(
        self, db_session: Session, getter: str, setter: str, default: bool
    ) -> None:
        getattr(runtime_config, setter)(db_session, not default)

        assert getattr(runtime_config, getter)(db_session) is (not default)


class TestMaskSecret:
    def test_mask_secret_short_values_are_fully_masked(self) -> None:
        assert runtime_config._mask_secret("") == ""  # noqa: SLF001
        assert runtime_config._mask_secret("short") == "*****"  # noqa: SLF001

    def test_mask_secret_keeps_only_the_ends_of_a_long_value(self) -> None:
        masked = runtime_config._mask_secret("AKIAABCDEFGHIJKLMNOP")  # noqa: SLF001

        assert masked.startswith("AKIA")
        assert masked.endswith("MNOP")
        assert "*" in masked
