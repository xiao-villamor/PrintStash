"""Nobody may run on the shipped JWT secret.

It is published in the repo, in `.env.example` and in the compose defaults, so
anyone can mint an admin token for an install that never overrode it. On boot we
replace it with a generated secret persisted in ``system_config``, rather than
refusing to start — a self-hoster who upgrades must not find a dead container.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.config import DEFAULT_JWT_SECRET, _overlay, settings
from app.services.runtime_config import ensure_jwt_secret, get_or_create


@pytest.fixture(autouse=True)
def _default_secret(monkeypatch: pytest.MonkeyPatch):
    """Pin the frozen setting: the dev shell may export VAULT_JWT_SECRET, and
    these tests are about what happens when nobody does."""
    monkeypatch.setattr(settings._frozen, "jwt_secret", DEFAULT_JWT_SECRET)
    yield
    _overlay.pop("jwt_secret", None)


def test_generates_and_persists_secret_when_default(db_session: Session) -> None:
    ensure_jwt_secret(db_session)

    stored = get_or_create(db_session).jwt_secret
    assert stored and stored != DEFAULT_JWT_SECRET
    assert len(stored) >= 32
    assert settings.jwt_secret == stored, "generated secret must be the effective one"


def test_generated_secret_is_stable_across_restarts(db_session: Session) -> None:
    ensure_jwt_secret(db_session)
    first = settings.jwt_secret

    _overlay.pop("jwt_secret", None)  # simulate a restart
    ensure_jwt_secret(db_session)

    assert settings.jwt_secret == first, "a restart must not invalidate every session"


def test_env_supplied_secret_is_left_alone(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who sets VAULT_JWT_SECRET stays in charge of it."""
    monkeypatch.setattr(settings._frozen, "jwt_secret", "operator-chosen-secret")

    ensure_jwt_secret(db_session)

    assert settings.jwt_secret == "operator-chosen-secret"
    assert get_or_create(db_session).jwt_secret is None, "must not persist env secrets"


@pytest.mark.parametrize(
    "unsafe_secret",
    ["", " \t ", f" {DEFAULT_JWT_SECRET} "],
)
def test_blank_or_disguised_default_secret_is_replaced(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_secret: str,
) -> None:
    monkeypatch.setattr(settings._frozen, "jwt_secret", unsafe_secret)

    ensure_jwt_secret(db_session)

    stored = get_or_create(db_session).jwt_secret
    assert stored
    assert len(stored) >= 32
    assert settings.jwt_secret == stored
    assert settings.jwt_secret != unsafe_secret


def test_two_installs_get_different_secrets(db_session: Session) -> None:
    ensure_jwt_secret(db_session)
    first = settings.jwt_secret

    config = get_or_create(db_session)
    config.jwt_secret = None
    db_session.add(config)
    db_session.commit()
    _overlay.pop("jwt_secret", None)

    ensure_jwt_secret(db_session)
    assert settings.jwt_secret != first
