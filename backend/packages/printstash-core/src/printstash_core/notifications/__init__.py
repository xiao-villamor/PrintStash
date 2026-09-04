"""Framework-neutral notification rendering surface."""

from .contracts import (
    NotificationEventType,
    NotificationTarget,
    OutboundRequest,
    RenderError,
)
from .renderers import (
    RENDERERS,
    TELEGRAM_API_BASE,
    event_label,
    render,
    render_discord,
    render_ntfy,
    render_telegram,
    render_webhook,
    summary_lines,
)

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
