"""Pure notification-event to outbound-request renderers."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from collections.abc import Callable
from email.header import Header
from typing import Any, cast

from .contracts import (
    NotificationEventType,
    NotificationTarget,
    OutboundRequest,
    RenderError,
)

# Kept configurable so consumers and contract tests can route Telegram traffic
# through an egress proxy or fake Bot API.
TELEGRAM_API_BASE = "https://api.telegram.org"

NotificationContext = dict[str, Any]
NotificationConfig = dict[str, Any]
Renderer = Callable[[NotificationContext, NotificationConfig], OutboundRequest]

_EVENT_LABELS: dict[NotificationEventType, str] = {
    NotificationEventType.PRINT_COMPLETED: "Print completed",
    NotificationEventType.PRINT_FAILED: "Print failed",
    NotificationEventType.PRINT_CANCELLED: "Print cancelled",
    NotificationEventType.PRINTER_OFFLINE: "Printer offline",
}
_EVENT_COLORS: dict[NotificationEventType, int] = {
    NotificationEventType.PRINT_COMPLETED: 0x2ECC71,
    NotificationEventType.PRINT_FAILED: 0xE74C3C,
    NotificationEventType.PRINT_CANCELLED: 0xF39C12,
    NotificationEventType.PRINTER_OFFLINE: 0x95A5A6,
}
_EVENT_NTFY: dict[NotificationEventType, tuple[str, str]] = {
    NotificationEventType.PRINT_COMPLETED: ("default", "white_check_mark"),
    NotificationEventType.PRINT_FAILED: ("high", "x"),
    NotificationEventType.PRINT_CANCELLED: ("default", "warning"),
    NotificationEventType.PRINTER_OFFLINE: ("high", "satellite"),
}
_EVENT_EMOJI: dict[NotificationEventType, str] = {
    NotificationEventType.PRINT_COMPLETED: "✅",
    NotificationEventType.PRINT_FAILED: "❌",
    NotificationEventType.PRINT_CANCELLED: "⚠️",
    NotificationEventType.PRINTER_OFFLINE: "📡",
}


def _event_type(context: NotificationContext) -> NotificationEventType:
    return NotificationEventType(context["event"])


def _event_emoji(context: NotificationContext) -> str:
    return _EVENT_EMOJI.get(_event_type(context), "🔔")


def _model_url(context: NotificationContext) -> str | None:
    """Return a model URL only when it is safe to embed as an HTTP link."""
    url = context.get("model_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def event_label(context: NotificationContext) -> str:
    """Return the human-facing label for the context's event type."""
    return cast(
        str,
        _EVENT_LABELS.get(_event_type(context), context.get("event", "Event")),
    )


def _fmt_duration(seconds: int | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def summary_lines(context: NotificationContext) -> list[str]:
    """Build human-readable detail lines shared by text-based targets."""
    lines: list[str] = []
    printer = context.get("printer_name") or context.get("printer_id")
    if printer:
        lines.append(f"Printer: {printer}")
    if context.get("model_name"):
        lines.append(f"Model: {context['model_name']}")
    if context.get("filename"):
        lines.append(f"File: {context['filename']}")
    duration = _fmt_duration(context.get("duration_s"))
    if duration:
        lines.append(f"Duration: {duration}")
    if context.get("filament_used_g"):
        lines.append(f"Filament: {round(float(context['filament_used_g']), 1)} g")
    if context.get("error"):
        lines.append(f"Error: {context['error']}")
    return lines


def _title(context: NotificationContext) -> str:
    label = event_label(context)
    printer = context.get("printer_name")
    return f"{label} — {printer}" if printer else label


def _require(config: NotificationConfig, key: str, target: str) -> str:
    value = config.get(key)
    if not value or not str(value).strip():
        raise RenderError(f"{target} channel is missing required config '{key}'")
    return str(value).strip()


def render_webhook(
    context: NotificationContext,
    config: NotificationConfig,
) -> OutboundRequest:
    """Render a generic JSON webhook, optionally signed with HMAC-SHA256."""
    url = _require(config, "url", "webhook")
    body = json.dumps(
        {"event": context.get("event"), "data": context},
        separators=(",", ":"),
        sort_keys=True,
    )
    headers = {"Content-Type": "application/json"}
    secret = config.get("secret")
    if secret:
        digest = hmac.new(
            str(secret).encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["X-PrintStash-Signature"] = f"sha256={digest}"
    return OutboundRequest(method="POST", url=url, headers=headers, data=body)


def render_discord(
    context: NotificationContext,
    config: NotificationConfig,
) -> OutboundRequest:
    """Render a Discord webhook containing one event embed."""
    url = _require(config, "url", "discord")
    event_type = _event_type(context)
    fields = [
        {
            "name": part.split(": ", 1)[0],
            "value": part.split(": ", 1)[1],
            "inline": True,
        }
        for part in summary_lines(context)
        if ": " in part
    ]
    embed: dict[str, Any] = {
        "title": f"{_event_emoji(context)} {_title(context)}",
        "color": _EVENT_COLORS.get(event_type, 0x95A5A6),
        "footer": {"text": "PrintStash"},
    }
    model_url = _model_url(context)
    if model_url:
        embed["url"] = model_url
    if fields:
        embed["fields"] = fields
    if context.get("timestamp"):
        embed["timestamp"] = context["timestamp"]
    return OutboundRequest(
        method="POST",
        url=url,
        headers={"Content-Type": "application/json"},
        json={"embeds": [embed]},
    )


def render_telegram(
    context: NotificationContext,
    config: NotificationConfig,
    *,
    api_base: str | None = None,
) -> OutboundRequest:
    """Render a Telegram Bot API ``sendMessage`` request using safe HTML."""
    token = _require(config, "bot_token", "telegram")
    chat_id = _require(config, "chat_id", "telegram")
    text = f"{_event_emoji(context)} <b>{html.escape(_title(context))}</b>"
    lines = summary_lines(context)
    if lines:
        text += "\n" + "\n".join(html.escape(line) for line in lines)
    model_url = _model_url(context)
    if model_url:
        text += f'\n🔗 <a href="{html.escape(model_url, quote=True)}">View model</a>'
    resolved_api_base = TELEGRAM_API_BASE if api_base is None else api_base
    return OutboundRequest(
        method="POST",
        url=f"{resolved_api_base}/bot{token}/sendMessage",
        headers={"Content-Type": "application/json"},
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )


def _header_safe(value: str) -> str:
    """Encode a Unicode value for a transport-safe HTTP header."""
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def render_ntfy(
    context: NotificationContext,
    config: NotificationConfig,
) -> OutboundRequest:
    """Render an ntfy request with title, priority, tags, and tap action."""
    topic = _require(config, "topic", "ntfy")
    server = (config.get("server_url") or "https://ntfy.sh").rstrip("/")
    priority, tags = _EVENT_NTFY.get(_event_type(context), ("default", "bell"))
    headers = {
        "Title": _header_safe(_title(context)),
        "Priority": priority,
        "Tags": _header_safe(tags),
        "Content-Type": "text/plain; charset=utf-8",
    }
    token = config.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    model_url = _model_url(context)
    if model_url:
        try:
            model_url.encode("latin-1")
            headers["Click"] = model_url
            headers["Actions"] = f"view, View model, {model_url}"
        except UnicodeEncodeError:
            pass
    body = "\n".join(summary_lines(context)) or event_label(context)
    return OutboundRequest(
        method="POST",
        url=f"{server}/{topic}",
        headers=headers,
        data=body,
    )


RENDERERS: dict[NotificationTarget, Renderer] = {
    NotificationTarget.WEBHOOK: render_webhook,
    NotificationTarget.DISCORD: render_discord,
    NotificationTarget.TELEGRAM: render_telegram,
    NotificationTarget.NTFY: render_ntfy,
}


def render(
    target: NotificationTarget,
    context: NotificationContext,
    config: NotificationConfig,
) -> OutboundRequest:
    """Render ``context`` for ``target`` using its channel configuration."""
    try:
        renderer = RENDERERS[target]
    except KeyError as exc:  # pragma: no cover - guarded by enum contracts
        raise RenderError(f"no renderer registered for target {target}") from exc
    return renderer(context, config)
