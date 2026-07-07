"""Log in to MakerWorld (Bambu Lab account) to obtain a download session token.

MakerWorld auth-gates its file downloads. Rather than asking an admin to hand-
extract a browser cookie, this drives Bambu Lab's account API — the same login
flow OrcaSlicer / Bambu Studio use — to exchange email + password for an access
token (a JWT). MakerWorld's web session authenticates with that JWT under the
``token`` cookie, so the obtained token plugs straight into the importer's
existing cookie-injection path (see :mod:`app.services.browser_fetch`).

Bambu almost always emails a verification code on a login from a new device, and
every call from this server looks like a new device, so the flow is two-step:

1. :func:`begin_login` posts the credentials. Bambu either returns a token
   outright (rare) or signals that a code is required — by email
   (``need_email_code``) or authenticator app (``need_tfa_code``). The pending
   state (account, and the ``tfaKey`` for app-based 2FA) is held in-process under
   a short-lived ``login_token`` so the password is never stored.
2. :func:`submit_code` completes the login with the user-entered code and returns
   the access token.

All network shapes here are Bambu's public contract (as used by the community
``pybambu`` / Home Assistant integration); everything else is ours. Failures
raise :class:`MakerWorldAuthError` with a stable ``code`` the API surfaces.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

# Bambu's account API (used by Bambu Studio / OrcaSlicer / pybambu).
_LOGIN_URL = "https://api.bambulab.com/v1/user-service/user/login"
# App-based 2FA (authenticator code) is completed on the website endpoint, which
# returns the token as a ``token`` Set-Cookie rather than in the JSON body.
_TFA_URL = "https://bambulab.com/api/sign-in/tfa"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_TIMEOUT = 30.0
_PENDING_TTL = 600.0  # 10 min to enter the emailed/app code


class MakerWorldAuthError(Exception):
    """Login failed. ``code`` is a stable identifier the API/UI can branch on."""

    def __init__(self, code: str, message: Optional[str] = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class _PendingLogin:
    account: str
    tfa_key: Optional[str]
    created_at: float = field(default_factory=time.time)


@dataclass
class LoginResult:
    """Outcome of a login step.

    ``status`` is one of:
    * ``ok`` — ``token`` holds the access token; login is complete.
    * ``need_email_code`` / ``need_tfa_code`` — a code was sent / is required;
      ``login_token`` references the pending state to pass to :func:`submit_code`.
    """

    status: str
    token: Optional[str] = None
    login_token: Optional[str] = None


# In-process pending-login store (short TTL). Mirrors the registries in
# api/v1/ingest.py: a single-process interactive flow, no DB persistence needed.
_pending: dict[str, _PendingLogin] = {}


def _prune() -> None:
    cutoff = time.time() - _PENDING_TTL
    for key in [k for k, v in _pending.items() if v.created_at < cutoff]:
        _pending.pop(key, None)


def _stash(pending: _PendingLogin) -> str:
    _prune()
    token = uuid.uuid4().hex
    _pending[token] = pending
    return token


def _headers() -> dict:
    return {
        "User-Agent": _BROWSER_UA,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _token_from_payload(data: dict) -> Optional[str]:
    """Pull a non-empty access token out of a Bambu login response."""
    for key in ("accessToken", "access_token", "token"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def begin_login(account: str, password: str) -> LoginResult:
    """Start a MakerWorld login; returns a token or asks for a verification code."""
    account = (account or "").strip()
    if not account or not password:
        raise MakerWorldAuthError("missing_credentials")

    client = get_http_client()
    try:
        resp = await client.post(
            _LOGIN_URL,
            json={"account": account, "password": password, "apiError": ""},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — network boundary
        logger.warning("bambu login request failed: %s", exc)
        raise MakerWorldAuthError("network_error") from exc

    if resp.status_code in (401, 403):
        raise MakerWorldAuthError("invalid_credentials")
    if resp.status_code != 200:
        raise MakerWorldAuthError("login_failed", f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise MakerWorldAuthError("login_failed", "non-JSON response") from exc

    token = _token_from_payload(data)
    if token:
        return LoginResult(status="ok", token=token)

    # No token yet → a second factor is required. Bambu signals which via
    # ``loginType`` ("verifyCode" = emailed code, "tfa" = authenticator app).
    login_type = str(data.get("loginType") or "").lower()
    tfa_key = data.get("tfaKey") or None
    if login_type == "tfa" or tfa_key:
        login_token = _stash(_PendingLogin(account=account, tfa_key=tfa_key))
        return LoginResult(status="need_tfa_code", login_token=login_token)
    if login_type in ("verifycode", "verify_code", "email"):
        login_token = _stash(_PendingLogin(account=account, tfa_key=None))
        return LoginResult(status="need_email_code", login_token=login_token)

    # Unrecognised shape with no token: treat as a (possibly emailed) code step
    # rather than a hard failure, so a new Bambu variant still completes.
    logger.warning("bambu login returned no token and no known loginType: %r", login_type)
    login_token = _stash(_PendingLogin(account=account, tfa_key=None))
    return LoginResult(status="need_email_code", login_token=login_token)


async def submit_code(login_token: str, code: str) -> LoginResult:
    """Complete a pending login with the emailed / authenticator code."""
    code = (code or "").strip()
    if not code:
        raise MakerWorldAuthError("missing_code")
    _prune()
    pending = _pending.get(login_token)
    if pending is None:
        raise MakerWorldAuthError("login_expired")

    token = (
        await _submit_tfa(pending, code)
        if pending.tfa_key
        else await _submit_email_code(pending, code)
    )
    _pending.pop(login_token, None)
    return LoginResult(status="ok", token=token)


async def _submit_email_code(pending: _PendingLogin, code: str) -> str:
    """Email-code path: re-call the login endpoint with ``code`` instead of password."""
    client = get_http_client()
    try:
        resp = await client.post(
            _LOGIN_URL,
            json={"account": pending.account, "code": code},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — network boundary
        logger.warning("bambu code submit failed: %s", exc)
        raise MakerWorldAuthError("network_error") from exc

    if resp.status_code != 200:
        raise MakerWorldAuthError("invalid_code", f"HTTP {resp.status_code}")
    try:
        token = _token_from_payload(resp.json())
    except ValueError as exc:
        raise MakerWorldAuthError("invalid_code", "non-JSON response") from exc
    if not token:
        raise MakerWorldAuthError("invalid_code")
    return token


async def _submit_tfa(pending: _PendingLogin, code: str) -> str:
    """Authenticator path: POST the code+tfaKey; token comes back as a cookie."""
    client = get_http_client()
    try:
        resp = await client.post(
            _TFA_URL,
            json={"tfaCode": code, "tfaKey": pending.tfa_key},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — network boundary
        logger.warning("bambu tfa submit failed: %s", exc)
        raise MakerWorldAuthError("network_error") from exc

    if resp.status_code != 200:
        raise MakerWorldAuthError("invalid_code", f"HTTP {resp.status_code}")
    token = resp.cookies.get("token")
    if not token:
        # Some variants echo it in the body instead of Set-Cookie.
        try:
            token = _token_from_payload(resp.json())
        except ValueError:
            token = None
    if not token:
        raise MakerWorldAuthError("invalid_code")
    return token
