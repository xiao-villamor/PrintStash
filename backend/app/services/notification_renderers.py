"""Compatibility facade for framework-neutral notification renderers.

The application-facing registry remains keyed by the ORM enums, while all
payload construction lives in :mod:`printstash_core.notifications`.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from printstash_core import notifications as _core

from app.db.models import NotificationEventType, NotificationTarget

OutboundRequest = _core.OutboundRequest
RenderError = _core.RenderError

# Preserve the application override seam used by deployments and the E2E fake.
TELEGRAM_API_BASE = _core.TELEGRAM_API_BASE

event_label = _core.event_label
summary_lines = _core.summary_lines
render_webhook = _core.render_webhook
render_discord = _core.render_discord
render_ntfy = _core.render_ntfy


def render_telegram(context: Dict[str, Any], config: Dict[str, Any]) -> OutboundRequest:
    """Render Telegram through the application-configurable Bot API base."""
    return _core.render_telegram(context, config, api_base=TELEGRAM_API_BASE)


RENDERERS: Dict[
    NotificationTarget,
    Callable[[Dict[str, Any], Dict[str, Any]], OutboundRequest],
] = {
    NotificationTarget.WEBHOOK: render_webhook,
    NotificationTarget.DISCORD: render_discord,
    NotificationTarget.TELEGRAM: render_telegram,
    NotificationTarget.NTFY: render_ntfy,
}


def render(
    target: NotificationTarget,
    context: Dict[str, Any],
    config: Dict[str, Any],
) -> OutboundRequest:
    """Render ``context`` for an ORM notification target."""
    try:
        renderer = RENDERERS[target]
    except KeyError as exc:  # pragma: no cover - guarded by enum
        raise RenderError(f"no renderer registered for target {target}") from exc
    return renderer(context, config)


__all__ = [
    "NotificationEventType",
    "NotificationTarget",
    "OutboundRequest",
    "RENDERERS",
    "RenderError",
    "TELEGRAM_API_BASE",
    "event_label",
    "render",
    "render_discord",
    "render_ntfy",
    "render_telegram",
    "render_webhook",
    "summary_lines",
]
