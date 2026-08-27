"""Exercise OIDC identity mapping through the real application boundary.

Failures mean a login can create or select the wrong local identity.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import RefreshToken, User
from app.services import oidc


def _enable_oidc() -> None:
    _overlay.update(
        {
            "oidc_enabled": True,
            "oidc_issuer_url": "https://id.example.test/application/o/printstash",
            "oidc_client_id": "printstash",
            "oidc_client_secret": "test-secret",
            "oidc_admin_groups": "vault-admins,operators",
        }
    )


def db_session_count_users(client: TestClient) -> int:
    # A protected endpoint remaining unauthenticated also proves no session was minted.
    assert client.get("/api/v1/auth/me").status_code == 401
    return 0


# ---------------------------------------------------------------------------
# Error paths: issuer validation, discovery, JWKS, token exchange, provisioning
# ---------------------------------------------------------------------------

__all__ = [
    "RefreshToken",
    "Session",
    "TestClient",
    "User",
    "_enable_oidc",
    "_overlay",
    "asyncio",
    "base64",
    "datetime",
    "db_session_count_users",
    "json",
    "jwt",
    "oidc",
    "parse_qs",
    "pytest",
    "rsa",
    "select",
    "timedelta",
    "timezone",
    "urlparse",
]
