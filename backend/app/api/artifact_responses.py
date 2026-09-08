"""Render framework-free delivery plans after route authentication."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.types import Receive, Scope, Send

from app.modules.storage.artifact_delivery import (
    DeliveryPlan,
    DeliveryPurpose,
    DeliveryRequest,
)


def delivery_request(
    request: Request | None,
    filename: str,
    media_type: str = "application/octet-stream",
    purpose: DeliveryPurpose = DeliveryPurpose.DOWNLOAD,
) -> DeliveryRequest:
    headers = request.headers if request is not None else {}
    fetch = headers.get("sec-fetch-mode") in {"cors", "same-origin"}
    origin = headers.get("origin")
    if origin is None and request is not None and fetch:
        origin = str(request.base_url).rstrip("/")
    return DeliveryRequest(
        filename=filename,
        media_type=media_type,
        purpose=purpose,
        inline=purpose == DeliveryPurpose.THUMBNAIL,
        origin=origin,
        application_origin=(str(request.base_url).rstrip("/") if request else None),
        if_none_match=headers.get("if-none-match"),
        if_modified_since=headers.get("if-modified-since"),
        range_header=headers.get("range"),
        if_range=headers.get("if-range"),
        proxy_only=headers.get("x-printstash-delivery") == "proxy",
    )


def _render_delivery(plan: DeliveryPlan) -> Response:
    if plan.redirect is not None:
        return RedirectResponse(plan.redirect, status_code=307, headers=plan.headers)
    if plan.path is not None:
        return FileResponse(plan.path, media_type=plan.media_type, headers=plan.headers)
    if plan.chunks is not None:
        return StreamingResponse(
            plan.chunks,
            status_code=plan.status,
            media_type=plan.media_type,
            headers=plan.headers,
        )
    return Response(status_code=plan.status, headers=plan.headers)


class _LeasedResponse(Response):
    """Keep a selected path alive even if the client disconnects mid-response."""

    def __init__(self, response: Response, plan: DeliveryPlan):
        self.response = response
        self.plan = plan
        self.status_code = response.status_code
        self.raw_headers = response.raw_headers
        self.background = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.response(scope, receive, send)
        finally:
            if self.plan.close is not None:
                self.plan.close()


def render_delivery(plan: DeliveryPlan) -> Response:
    response = _render_delivery(plan)
    return _LeasedResponse(response, plan) if plan.close is not None else response


def serve_stored_file(
    key: str,
    filename: str,
    media_type: str = "application/octet-stream",
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Render authorized non-Artifact resources through the delivery owner."""
    from app.modules.storage.artifact_delivery import plan_stored_representation
    from app.modules.storage.storage_backend.runtime import get_backend

    plan = plan_stored_representation(
        get_backend(), key, DeliveryRequest(filename, media_type, proxy_only=True)
    )
    plan.headers.update(headers or {})
    return render_delivery(plan)
