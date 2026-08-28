"""The two ways a request can look authenticated without being authorised.

A valid token is not a licence to write. The scope carried in it is separate from
who the user is, so a `read` token belonging to an admin must be refused by a
write endpoint — and a subject claim that is missing or not a number is a token
that cannot identify anybody, which must fail rather than resolve to whichever
user id a loose cast produces.

Both are small checks in front of every mutating endpoint, which is exactly why
they get their own rows: a regression here is not visible in any single
endpoint's tests.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.core.security import get_current_user, require_auth
from tests.factories import user_config


class TestRequireAuth:
    def test_require_auth_rejects_authenticated_user_without_write_scope(self) -> None:
        user = user_config("reader")

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_auth(user, {"scope": "read"}))

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "insufficient_scope"


class TestUser:
    def test_current_user_rejects_missing_or_non_numeric_subject(
        self,
        db_session: Session,
    ) -> None:
        assert get_current_user({"scope": "read"}, db_session) is None
        assert get_current_user({"sub": "not-an-id"}, db_session) is None
