"""Direct unit tests for app.services.runtime_config.

Most of this module is already exercised indirectly through the /api/v1/config
endpoint tests (test_oidc.py, test_api_hardening.py, ...), but several
functions — apply_overlay's full field sweep, ensure_jwt_secret's three
branches, update_config's int/bool sentinel handling, the makerworld token
set/clear cycle, and _mask_secret's edge cases — had no direct coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import DEFAULT_JWT_SECRET, _overlay, settings
from app.db.models import User
from app.services import runtime_config


@pytest.fixture(autouse=True)
def _clean_overlay():
    # runtime_config writes directly into the shared overlay dict; keep tests
    # isolated from each other and from whatever the app fixtures left behind.
    saved = dict(_overlay)
    yield
    _overlay.clear()
    _overlay.update(saved)


def test_get_or_create_creates_singleton_row_once(db_session: Session) -> None:
    first = runtime_config.get_or_create(db_session)
    assert first.id == 1
    second = runtime_config.get_or_create(db_session)
    assert second.id == first.id


def test_is_configured_false_without_configured_at_or_users(db_session: Session) -> None:
    assert runtime_config.is_configured(db_session) is False


def test_is_configured_false_when_configured_but_no_users(db_session: Session) -> None:
    from app.core.time import utcnow

    config = runtime_config.get_or_create(db_session)
    config.configured_at = utcnow()
    db_session.add(config)
    db_session.commit()

    # configured_at alone isn't enough — is_configured also requires a user.
    assert runtime_config.is_configured(db_session) is False


def test_is_configured_true_once_configured_and_a_user_exists(
    db_session: Session,
) -> None:
    from app.core.time import utcnow
    from app.services.auth import hash_password

    config = runtime_config.get_or_create(db_session)
    config.configured_at = utcnow()
    db_session.add(config)
    db_session.add(
        User(
            username="setup-admin",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
    )
    db_session.commit()

    assert runtime_config.is_configured(db_session) is True


def test_mark_configured_is_idempotent(db_session: Session) -> None:
    first = runtime_config.mark_configured(db_session)
    assert first.configured_at is not None
    stamp = first.configured_at

    second = runtime_config.mark_configured(db_session)
    assert second.configured_at == stamp  # not overwritten on a second call


def test_apply_overlay_noop_when_no_config_row(db_session: Session) -> None:
    _overlay["storage_backend"] = "leftover"
    runtime_config.apply_overlay(db_session)
    # No SystemConfig row exists yet — apply_overlay must not clear/touch the overlay.
    assert _overlay.get("storage_backend") == "leftover"


def test_apply_overlay_copies_all_persisted_fields(db_session: Session) -> None:
    config = runtime_config.get_or_create(db_session)
    config.data_dir = "/data/vault"
    config.thumb_dir = "/data/thumbs"
    config.storage_backend = "s3"
    config.s3_bucket = "my-bucket"
    config.s3_endpoint_url = "https://s3.example.test"
    config.s3_region = "us-east-1"
    config.s3_access_key = "AKIA_TEST"
    config.s3_secret_key = "secret-value"
    config.backup_retention_days = 45
    config.trash_retention_days = 10
    config.backup_s3_bucket = "backup-bucket"
    config.oidc_enabled = True
    config.oidc_issuer_url = "https://idp.example.test"
    config.oidc_client_id = "client-123"
    config.oidc_client_secret = "client-secret"
    config.oidc_allow_insecure_http = True
    config.makerworld_token = "mw-token-abc"
    db_session.add(config)
    db_session.commit()

    runtime_config.apply_overlay(db_session)

    assert _overlay["data_dir"] == Path("/data/vault")
    assert _overlay["thumb_dir"] == Path("/data/thumbs")
    assert _overlay["storage_backend"] == "s3"
    assert _overlay["s3_bucket"] == "my-bucket"
    assert _overlay["backup_retention_days"] == 45
    assert _overlay["trash_retention_days"] == 10
    assert _overlay["oidc_enabled"] is True
    assert _overlay["oidc_client_secret"] == "client-secret"
    assert _overlay["oidc_allow_insecure_http"] is True
    assert _overlay["makerworld_cookie"] == "token=mw-token-abc"


def test_apply_overlay_clears_stale_keys_not_in_config(db_session: Session) -> None:
    _overlay["some_stale_key_from_a_prior_boot"] = "gone"
    runtime_config.get_or_create(db_session)  # empty row: every _set() is a no-op

    runtime_config.apply_overlay(db_session)

    assert "some_stale_key_from_a_prior_boot" not in _overlay


def test_ensure_jwt_secret_noop_when_env_var_is_set(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings._frozen, "jwt_secret", "an-operator-set-secret")  # noqa: SLF001
    runtime_config.ensure_jwt_secret(db_session)
    assert "jwt_secret" not in _overlay


def test_ensure_jwt_secret_reuses_persisted_secret(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings._frozen, "jwt_secret", DEFAULT_JWT_SECRET)  # noqa: SLF001
    config = runtime_config.get_or_create(db_session)
    config.jwt_secret = "already-persisted-secret"
    db_session.add(config)
    db_session.commit()

    runtime_config.ensure_jwt_secret(db_session)

    assert _overlay["jwt_secret"] == "already-persisted-secret"


def test_ensure_jwt_secret_generates_and_persists_when_missing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings._frozen, "jwt_secret", DEFAULT_JWT_SECRET)  # noqa: SLF001

    runtime_config.ensure_jwt_secret(db_session)

    generated = _overlay["jwt_secret"]
    assert generated and generated != DEFAULT_JWT_SECRET
    stored = runtime_config.get_or_create(db_session)
    assert stored.jwt_secret == generated


def test_update_config_int_field_clear_falls_back_to_env_default(
    db_session: Session,
) -> None:
    runtime_config.update_config(db_session, backup_retention_days=45)
    assert _overlay["backup_retention_days"] == 45

    # -1 is the "clear override" sentinel for int fields.
    runtime_config.update_config(db_session, backup_retention_days=-1)
    config = runtime_config.get_or_create(db_session)
    assert config.backup_retention_days is None
    assert isinstance(_overlay["backup_retention_days"], int)


def test_update_config_str_field_empty_string_clears_override(
    db_session: Session,
) -> None:
    runtime_config.update_config(db_session, oidc_issuer_url="https://idp.example.test")
    assert _overlay["oidc_issuer_url"] == "https://idp.example.test"

    runtime_config.update_config(db_session, oidc_issuer_url="")
    config = runtime_config.get_or_create(db_session)
    assert config.oidc_issuer_url is None


def test_update_config_bool_field_round_trips(db_session: Session) -> None:
    runtime_config.update_config(db_session, oidc_enabled=True)
    assert _overlay["oidc_enabled"] is True
    config = runtime_config.get_or_create(db_session)
    assert config.oidc_enabled is True

    runtime_config.update_config(db_session, oidc_enabled=False)
    assert _overlay["oidc_enabled"] is False


def test_update_config_data_dir_clear_uses_env_default_path(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # update_config calls ensure_dirs() as a side effect; stub it out so this
    # stays a unit test of the overlay/DB bookkeeping, not real mkdir calls
    # against whatever absolute default path Settings happens to carry.
    monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)

    runtime_config.update_config(db_session, data_dir="/custom/data")
    assert _overlay["data_dir"] == Path("/custom/data")

    runtime_config.update_config(db_session, data_dir="")
    # Falls back to the env/default Settings value, still coerced to a Path.
    assert isinstance(_overlay["data_dir"], Path)


def test_makerworld_token_set_and_clear_cycle(db_session: Session) -> None:
    status_before = runtime_config.makerworld_status(db_session)
    assert status_before == {"connected": False, "updated_at": None}

    runtime_config.set_makerworld_token(db_session, "session-token-xyz")
    assert _overlay["makerworld_cookie"] == "token=session-token-xyz"
    status_connected = runtime_config.makerworld_status(db_session)
    assert status_connected["connected"] is True
    assert status_connected["updated_at"] is not None

    runtime_config.clear_makerworld_token(db_session)
    assert "makerworld_cookie" not in _overlay
    assert runtime_config.makerworld_status(db_session)["connected"] is False


def test_spoolman_config_set_and_get_respects_unset_sentinel(
    db_session: Session,
) -> None:
    runtime_config.set_spoolman_config(
        db_session, base_url="http://spoolman.local:7912/", api_key="secret-key"
    )
    got = runtime_config.spoolman_config(db_session)
    assert got == {"base_url": "http://spoolman.local:7912", "api_key": "secret-key"}

    # Passing only base_url must not clobber the previously stored api_key.
    runtime_config.set_spoolman_config(db_session, base_url="http://spoolman.local:9999")
    got2 = runtime_config.spoolman_config(db_session)
    assert got2["api_key"] == "secret-key"
    assert got2["base_url"] == "http://spoolman.local:9999"


def test_spoolman_config_defaults_when_no_row(db_session: Session) -> None:
    assert runtime_config.spoolman_config(db_session) == {"base_url": None, "api_key": None}


def test_currency_defaults_to_usd_and_round_trips(db_session: Session) -> None:
    assert runtime_config.currency(db_session) == "USD"
    runtime_config.set_currency(db_session, "eur")
    assert runtime_config.currency(db_session) == "EUR"


def test_boolean_toggles_default_and_round_trip(db_session: Session) -> None:
    assert runtime_config.auto_mark_known_good_enabled(db_session) is True
    assert runtime_config.external_libraries_enabled(db_session) is False
    assert runtime_config.notifications_enabled(db_session) is False
    assert runtime_config.spoolman_enabled(db_session) is False
    assert runtime_config.spoolman_write_enabled(db_session) is False
    assert runtime_config.spoolman_write_force(db_session) is False

    runtime_config.set_auto_mark_known_good(db_session, False)
    runtime_config.set_external_libraries_enabled(db_session, True)
    runtime_config.set_notifications_enabled(db_session, True)
    runtime_config.set_spoolman_enabled(db_session, True)
    runtime_config.set_spoolman_write_enabled(db_session, True)
    runtime_config.set_spoolman_write_force(db_session, True)

    assert runtime_config.auto_mark_known_good_enabled(db_session) is False
    assert runtime_config.external_libraries_enabled(db_session) is True
    assert runtime_config.notifications_enabled(db_session) is True
    assert runtime_config.spoolman_enabled(db_session) is True
    assert runtime_config.spoolman_write_enabled(db_session) is True
    assert runtime_config.spoolman_write_force(db_session) is True


def test_get_effective_config_masks_secrets_but_reports_presence(
    db_session: Session,
) -> None:
    runtime_config.update_config(
        db_session,
        s3_access_key="AKIAABCDEFGHIJKLMNOP",
        s3_secret_key="short",
        oidc_client_secret="oidc-secret-value",
    )

    effective = runtime_config.get_effective_config(db_session)

    assert effective["has_s3_access_key"] is True
    assert "AKIAABCDEFGHIJKLMNOP" not in effective["s3_access_key"]
    assert effective["s3_access_key"].startswith("AKIA")
    assert effective["has_oidc_client_secret"] is True
    assert effective["oidc_enabled"] is False


def test_mask_secret_short_values_are_fully_masked() -> None:
    assert runtime_config._mask_secret("") == ""  # noqa: SLF001
    assert runtime_config._mask_secret("short") == "*****"  # noqa: SLF001


def test_mask_secret_long_values_keep_head_and_tail() -> None:
    masked = runtime_config._mask_secret("AKIAABCDEFGHIJKLMNOP")  # noqa: SLF001
    assert masked.startswith("AKIA")
    assert masked.endswith("MNOP")
    assert "*" in masked
