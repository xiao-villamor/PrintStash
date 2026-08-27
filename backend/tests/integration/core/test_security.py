"""Defends ``test_current_user_rejects_missing_or_non_numeric_subject`` behavior for the ``core`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.core.security import get_current_user, require_auth
from app.db.models import User


def test_current_user_rejects_missing_or_non_numeric_subject(
    db_session: Session,
) -> None:
    assert get_current_user({"scope": "read"}, db_session) is None
    assert get_current_user({"sub": "not-an-id"}, db_session) is None


def test_require_auth_rejects_authenticated_user_without_write_scope() -> None:
    user = User(username="reader", hashed_password="unused", is_active=True)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_auth(user, {"scope": "read"}))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "insufficient_scope"
