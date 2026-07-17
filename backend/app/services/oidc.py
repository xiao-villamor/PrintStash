"""Generic OpenID Connect authorization-code login with PKCE."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
from jwt import InvalidTokenError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.time import utcnow
from app.db.models import User


class OIDCError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ALLOWED_ID_TOKEN_ALGORITHMS = {
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
}


@dataclass(frozen=True)
class OIDCLogin:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str


def enabled() -> bool:
    return bool(
        settings.oidc_enabled and settings.oidc_issuer_url and settings.oidc_client_id
    )


def provider_status() -> dict[str, str | bool]:
    return {"enabled": enabled(), "display_name": settings.oidc_display_name}


def _issuer() -> str:
    issuer = settings.oidc_issuer_url.rstrip("/")
    parsed = urlparse(issuer)
    allowed = parsed.scheme == "https" or (
        settings.oidc_allow_insecure_http and parsed.scheme == "http"
    )
    if not allowed or not parsed.netloc:
        raise OIDCError("oidc_invalid_issuer")
    return issuer


async def _get_json(url: str) -> dict[str, Any]:
    response = await get_http_client().get(url, timeout=10.0)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise OIDCError("oidc_invalid_response")
    return value


async def _post_token(
    url: str,
    payload: dict[str, str],
    *,
    basic_auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    response = await get_http_client().post(
        url, data=payload, auth=basic_auth, timeout=10.0
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise OIDCError("oidc_invalid_response")
    return value


async def _discovery() -> dict[str, Any]:
    issuer = _issuer()
    try:
        document = await _get_json(f"{issuer}/.well-known/openid-configuration")
    except OIDCError:
        raise
    except Exception as exc:
        raise OIDCError("oidc_discovery_failed") from exc
    if document.get("issuer", "").rstrip("/") != issuer:
        raise OIDCError("oidc_issuer_mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = document.get(key)
        parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
        allowed = parsed and (
            parsed.scheme == "https"
            or (settings.oidc_allow_insecure_http and parsed.scheme == "http")
        )
        if not allowed or not parsed.netloc:
            raise OIDCError("oidc_invalid_discovery")
    return document


def callback_uri(request_uri: str) -> str:
    return settings.oidc_redirect_uri or request_uri


async def begin_login(redirect_uri: str) -> OIDCLogin:
    if not enabled():
        raise OIDCError("oidc_not_configured")
    document = await _discovery()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.oidc_scopes.replace(",", " ").split()),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return OIDCLogin(
        authorization_url=f"{document['authorization_endpoint']}?{urlencode(params)}",
        state=state,
        nonce=nonce,
        code_verifier=verifier,
    )


def _signing_key(jwks: dict[str, Any], token: str) -> jwt.PyJWK:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm not in _ALLOWED_ID_TOKEN_ALGORITHMS:
        raise OIDCError("oidc_invalid_id_token_algorithm")
    kid = header.get("kid")
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise OIDCError("oidc_invalid_jwks")
    for value in keys:
        if isinstance(value, dict) and (kid is None or value.get("kid") == kid):
            try:
                key = jwt.PyJWK.from_dict(value, algorithm=algorithm)
            except Exception:
                continue
            return key
    raise OIDCError("oidc_signing_key_not_found")


async def exchange_code(
    code: str, redirect_uri: str, verifier: str, nonce: str
) -> dict[str, Any]:
    document = await _discovery()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": verifier,
    }
    methods = document.get("token_endpoint_auth_methods_supported")
    if methods is not None and (
        not isinstance(methods, list) or not all(isinstance(value, str) for value in methods)
    ):
        raise OIDCError("oidc_invalid_discovery")
    use_basic_auth = bool(
        settings.oidc_client_secret
        and (methods is None or "client_secret_basic" in methods)
    )
    if settings.oidc_client_secret and not use_basic_auth:
        if methods is None or "client_secret_post" not in methods:
            raise OIDCError("oidc_unsupported_token_auth_method")
        payload["client_secret"] = settings.oidc_client_secret
    try:
        token_response = await _post_token(
            document["token_endpoint"],
            payload,
            basic_auth=(settings.oidc_client_id, settings.oidc_client_secret)
            if use_basic_auth
            else None,
        )
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("oidc_missing_id_token")
        jwks = await _get_json(document["jwks_uri"])
        key = _signing_key(jwks, id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=[key.algorithm_name],
            audience=settings.oidc_client_id,
            issuer=_issuer(),
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except OIDCError:
        raise
    except InvalidTokenError as exc:
        raise OIDCError("oidc_invalid_id_token") from exc
    except Exception as exc:
        raise OIDCError("oidc_token_exchange_failed") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("oidc_nonce_mismatch")
    audiences = claims.get("aud")
    authorized_party = claims.get("azp")
    if (
        isinstance(audiences, list)
        and len(audiences) > 1
        and not isinstance(authorized_party, str)
    ) or (
        authorized_party is not None
        and not secrets.compare_digest(str(authorized_party), settings.oidc_client_id)
    ):
        raise OIDCError("oidc_invalid_authorized_party")
    return claims


def _claim_path(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _unique_username(session: Session, candidate: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "-", candidate).strip("-._")[:96]
    base = base or "oidc-user"
    value = base
    suffix = 1
    while session.exec(select(User).where(User.username == value)).first() is not None:
        suffix += 1
        value = f"{base[:110]}-{suffix}"
    return value


def provision_user(session: Session, claims: dict[str, Any]) -> User:
    issuer = str(claims.get("iss", "")).rstrip("/")
    subject = str(claims.get("sub", ""))
    if not issuer or not subject or issuer != _issuer():
        raise OIDCError("oidc_invalid_identity")
    user = session.exec(
        select(User).where(
            User.oidc_issuer == issuer,
            User.oidc_subject == subject,
        )
    ).first()
    groups_value = _claim_path(claims, settings.oidc_groups_claim)
    groups = (
        {str(value) for value in groups_value}
        if isinstance(groups_value, list)
        else set()
    )
    admin_groups = {
        value.strip()
        for value in settings.oidc_admin_groups.split(",")
        if value.strip()
    }
    is_superuser = bool(groups & admin_groups)
    email_value = claims.get("email")
    email = str(email_value)[:255] if email_value else None
    if user is None:
        requested = _claim_path(claims, settings.oidc_username_claim)
        if not isinstance(requested, str) or not requested.strip():
            requested = email.split("@", 1)[0] if email else subject
        user = User(
            username=_unique_username(session, requested),
            email=email,
            hashed_password=f"!oidc:{secrets.token_urlsafe(32)}",
            is_superuser=is_superuser,
            is_active=True,
            oidc_issuer=issuer,
            oidc_subject=subject,
            oidc_managed=True,
        )
    else:
        if not user.is_active:
            raise OIDCError("oidc_user_inactive")
        user.email = email
        user.is_superuser = is_superuser
        user.updated_at = utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
