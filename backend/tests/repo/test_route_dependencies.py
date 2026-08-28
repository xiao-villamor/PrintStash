"""Write-capability guards on rich-capture mutation routes.

These assertions intentionally inspect the router seam instead of sending
requests through TestClient: this keeps the authorization contract focused and
avoids the app portal fixture used by the wider API tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute

from app.api.v1 import inbox, models, provider_connections
from app.core.browser_device_auth import require_user_or_browser_import_user
from app.core.security import require_auth
from app.db.models import User


def _routes(router: APIRouter) -> Iterable[APIRoute]:
    return (route for route in router.routes if isinstance(route, APIRoute))


def _route(router: APIRouter, path: str, method: str) -> APIRoute:
    return next(
        route
        for route in _routes(router)
        if route.path == path and method in (route.methods or set())
    )


def _has_dependency(route: APIRoute, dependency: object) -> bool:
    return any(
        getattr(item, "dependency", getattr(item, "call", None)) is dependency
        for item in (*route.dependencies, *route.dependant.dependencies)
    )


def _assert_read_token_is_rejected(route: APIRoute) -> None:
    assert _has_dependency(route, require_auth)
    with pytest.raises(HTTPException, match="insufficient_scope"):
        asyncio.run(
            require_auth(
                User(username="reader", hashed_password="unused", is_active=True),
                {"scope": "read"},
            )
        )


class TestRouteDependencies:
    """Every mutating route carries the dependency that gates it.

    A missing scope check is invisible from inside the route: it returns the
    right body for the right request and quietly accepts the wrong caller. So
    this reads the dependency list off each route rather than exercising it, and
    it is a repo invariant rather than a per-endpoint test because the failure
    mode is a route somebody *forgot*, which no per-endpoint file would cover."""

    def test_provider_connection_mutations_require_write_scope(self) -> None:
        for path, method in (
            ("/provider-connections/cults/connect", "POST"),
            ("/provider-connections/myminifactory/authorize", "POST"),
            ("/provider-connections/{provider}/disconnect", "DELETE"),
        ):
            _assert_read_token_is_rejected(
                _route(provider_connections.router, path, method)
            )

    def test_browser_pairing_management_mutations_require_write_scope(self) -> None:
        for path, method in (
            ("/browser-pairings", "POST"),
            ("/browser-pairings/{device_id}", "PATCH"),
            ("/browser-pairings/{device_id}", "DELETE"),
        ):
            _assert_read_token_is_rejected(
                _route(provider_connections.pairing_router, path, method)
            )

    def test_provenance_mutations_require_write_scope(self) -> None:
        for path, method in (
            ("/models/{model_id}/provenance/{source_id}", "PATCH"),
            ("/models/{model_id}/provenance/{source_id}/cover", "PUT"),
            ("/models/{model_id}/provenance/{source_id}/cover", "DELETE"),
        ):
            _assert_read_token_is_rejected(_route(models.router, path, method))

    def test_browser_credentials_are_limited_to_capture_routes(self) -> None:
        capture_routes = {
            (route.path, next(iter(list(route.methods or set()))))
            for route in _routes(inbox.router)
            if _has_dependency(route, require_user_or_browser_import_user)
        }

        assert capture_routes == {
            ("/inbox", "POST"),
            ("/inbox/capture-upload-slots", "POST"),
            ("/inbox/capture-upload-slots/{slot_id}", "PUT"),
            ("/inbox/{item_id}/capture-upload-finalize", "POST"),
            ("/inbox/browser-upload", "POST"),
        }
        assert not any(
            _has_dependency(route, require_user_or_browser_import_user)
            for router in (
                models.router,
                provider_connections.router,
                provider_connections.pairing_router,
            )
            for route in _routes(router)
        )
