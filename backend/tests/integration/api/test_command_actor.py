"""Detached command authentication retains the original credential authority."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlmodel import select
from starlette.requests import Request

from app.api import command_actor
from app.core.time import utcnow
from app.db.models import BrowserDevice
from app.modules.identity import auth


class TestCommandActor:
    @pytest.mark.parametrize("scope", ["write", "admin"])
    def test_writer_accepts_write_authority(self, make_user, headers_for, scope):
        owner = make_user()
        token = headers_for(owner, scope=scope)["Authorization"].removeprefix("Bearer ")
        payload = auth.verify_access_token(token)

        actor = command_actor.require_command_writer(payload=payload)

        with command_actor.command_session(actor) as (session, current):
            assert session.get(type(owner), owner.id).id == current.id == owner.id

    def test_writer_rejects_read_authority(self, make_user, headers_for):
        owner = make_user()
        token = headers_for(owner, scope="read")["Authorization"].removeprefix(
            "Bearer "
        )
        payload = auth.verify_access_token(token)

        with pytest.raises(HTTPException) as error:
            command_actor.require_command_writer(payload=payload)

        assert (error.value.status_code, error.value.detail) == (
            401,
            "insufficient_scope",
        )

    def test_rejects_expired_jwt_authority(self, make_user, headers_for, monkeypatch):
        owner = make_user()
        token = headers_for(owner)["Authorization"].removeprefix("Bearer ")
        payload = auth.verify_access_token(token)
        actor = command_actor.require_command_user(payload=payload)
        monkeypatch.setattr(
            command_actor, "utcnow", lambda: utcnow() + timedelta(days=1), raising=False
        )

        with pytest.raises(HTTPException) as error:
            with command_actor.command_session(actor):
                pytest.fail("expired authority reached the command")

        assert (error.value.status_code, error.value.detail) == (
            401,
            "not_authenticated",
        )

    def test_fractional_expiry_matches_canonical_jwt_authority(
        self, make_user, headers_for, monkeypatch
    ):
        import jwt

        from app.core.config import settings

        owner = make_user()
        token = headers_for(owner)["Authorization"].removeprefix("Bearer ")
        payload = auth.verify_access_token(token)
        expiry = payload["exp"]
        payload["exp"] = expiry + 0.75
        token = jwt.encode(
            payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        verified = auth.verify_access_token(token)
        assert verified is not None
        actor = command_actor.require_command_user(payload=verified)
        after_canonical_expiry = datetime.fromtimestamp(expiry + 0.5, tz=timezone.utc)

        class ExpirationClock:
            @classmethod
            def now(cls, tz=None):
                return after_canonical_expiry

        monkeypatch.setattr(command_actor, "utcnow", lambda: after_canonical_expiry)
        monkeypatch.setattr(jwt.api_jwt, "datetime", ExpirationClock)

        assert auth.verify_access_token(token) is None
        with pytest.raises(HTTPException) as error:
            with command_actor.command_session(actor):
                pytest.fail("fractionally expired authority reached the command")

        assert (error.value.status_code, error.value.detail) == (
            401,
            "not_authenticated",
        )

    def test_rejects_blocklisted_jwt_authority(
        self, make_user, headers_for, monkeypatch
    ):
        owner = make_user()
        token = headers_for(owner)["Authorization"].removeprefix("Bearer ")
        payload = auth.verify_access_token(token)
        actor = command_actor.require_command_user(payload=payload)
        monkeypatch.setattr(auth, "ACCESS_BLOCKLIST", {payload["jti"]})

        with pytest.raises(HTTPException) as error:
            with command_actor.command_session(actor):
                pytest.fail("blocked authority reached the command")

        assert (error.value.status_code, error.value.detail) == (
            401,
            "not_authenticated",
        )

    @pytest.mark.parametrize(
        ("attribute", "value"),
        [("is_active", False), ("auth_version", 1)],
        ids=["inactive", "version"],
    )
    def test_rejects_changed_user_authority(
        self, db_session, make_user, headers_for, attribute, value
    ):
        owner = make_user(auth_version=0)
        payload = auth.verify_access_token(
            headers_for(owner)["Authorization"].removeprefix("Bearer ")
        )
        actor = command_actor.require_command_user(payload=payload)
        setattr(owner, attribute, value)
        db_session.add(owner)
        db_session.commit()

        with pytest.raises(HTTPException) as error:
            with command_actor.command_session(actor):
                pytest.fail("revoked identity reached the command")

        assert (error.value.status_code, error.value.detail) == (
            401,
            "not_authenticated",
        )

    def test_rejects_revoked_browser_authority(self, client, user_headers, db_session):
        headers = user_headers()
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        claimed = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "Command browser"},
        )
        credential = claimed.json()["credential"]
        actor = command_actor.require_capture_actor(
            Request({"type": "http", "headers": []}), credential
        )
        device = db_session.exec(select(BrowserDevice)).one()
        device.revoked_at = utcnow()
        db_session.add(device)
        db_session.commit()

        with pytest.raises(HTTPException) as error:
            with command_actor.command_session(actor):
                pytest.fail("revoked device reached the command")

        assert (error.value.status_code, error.value.detail) == (
            401,
            "invalid_browser_credential",
        )
