"""A fake OpenID Connect IdP on loopback: discovery + JWKS + token endpoint.

Speaks the subset of the OIDC authorization-code flow that
``app/services/oidc.py`` actually drives: ``_discovery`` GETs
``/.well-known/openid-configuration``, ``exchange_code`` POSTs
``/token`` and GETs ``/jwks``, then verifies the returned ``id_token`` with a
real RS256 signature check (issuer/audience/nonce all enforced for real).

This fake does **not** implement the interactive ``authorization_endpoint``
(there is no browser in this test layer) -- the caller mints a code directly
via ``issue_code(claims)`` standing in for "the user logged in at the IdP and
consented", then drives the real callback with it. Everything downstream of
that (discovery, token exchange, JWKS fetch, signature/issuer/nonce
verification) is the real code path over a real loopback HTTP server.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


class FakeOIDCProvider:
    """Mutable state for one fake IdP instance."""

    def __init__(self) -> None:
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.kid = "e2e-signing-key"
        self.issuer = ""  # set once the loopback server is listening
        self._pending_codes: dict[str, dict[str, Any]] = {}

    def issue_code(self, claims: dict[str, Any]) -> str:
        """Register claims for a one-time code, standing in for user consent."""
        code = secrets.token_urlsafe(16)
        self._pending_codes[code] = claims
        return code

    def _jwk(self) -> dict[str, Any]:
        jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self._private_key.public_key())
        )
        jwk["kid"] = self.kid
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        return jwk

    def _mint_id_token(self, claims: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "iss": self.issuer,
            "aud": claims.get("aud", "printstash-e2e"),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            **claims,
        }
        return jwt.encode(
            payload, self._private_key, algorithm="RS256", headers={"kid": self.kid}
        )


def build_app(state: FakeOIDCProvider) -> Starlette:
    async def discovery(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "issuer": state.issuer,
                "authorization_endpoint": f"{state.issuer}/authorize",
                "token_endpoint": f"{state.issuer}/token",
                "jwks_uri": f"{state.issuer}/jwks",
            }
        )

    async def jwks(_request: Request) -> JSONResponse:
        return JSONResponse({"keys": [state._jwk()]})

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        code = str(form.get("code", ""))
        claims = state._pending_codes.pop(code, None)
        if claims is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        id_token = state._mint_id_token(claims)
        return JSONResponse(
            {
                "access_token": secrets.token_urlsafe(16),
                "id_token": id_token,
                "token_type": "Bearer",
            }
        )

    return Starlette(
        routes=[
            Route("/.well-known/openid-configuration", discovery),
            Route("/jwks", jwks),
            Route("/token", token, methods=["POST"]),
        ]
    )
