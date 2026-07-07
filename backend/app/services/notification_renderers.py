"""Per-target rendering of notification events into outbound HTTP requests.

Each renderer is a **pure function** ``(context, config) -> OutboundRequest``:
it maps a normalised event ``context`` (built in :mod:`app.services.notifications`)
and a channel's ``config`` dict into the HTTP request the dispatcher should
send. Keeping them side-effect-free makes payload shape directly unit-testable
without a network or a live channel.

The registry :data:`RENDERERS` maps a :class:`NotificationTarget` to its
renderer. Adding a target means adding one function and one registry entry.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
from dataclasses import dataclass, field
from email.header import Header
from typing import Any, Callable, Dict, Optional

from app.db.models import NotificationEventType, NotificationTarget


class RenderError(ValueError):
    """Raised when a channel's config is missing fields a target requires."""


# Base URL of the Telegram Bot API. A module constant (not hard-coded inline) so
# deployments behind an egress proxy — and the E2E suite's fake Bot API — can
# point it elsewhere without touching the renderer.
TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass
class OutboundRequest:
    """A target-agnostic description of the HTTP call to make."""

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    json: Optional[Any] = None
    data: Optional[str] = None


# Human-facing labels and Discord embed colours, keyed by event type.
_EVENT_LABELS: Dict[NotificationEventType, str] = {
    NotificationEventType.PRINT_COMPLETED: "Print completed",
    NotificationEventType.PRINT_FAILED: "Print failed",
    NotificationEventType.PRINT_CANCELLED: "Print cancelled",
    NotificationEventType.PRINTER_OFFLINE: "Printer offline",
}
_EVENT_COLORS: Dict[NotificationEventType, int] = {
    NotificationEventType.PRINT_COMPLETED: 0x2ECC71,  # green
    NotificationEventType.PRINT_FAILED: 0xE74C3C,  # red
    NotificationEventType.PRINT_CANCELLED: 0xF39C12,  # orange
    NotificationEventType.PRINTER_OFFLINE: 0x95A5A6,  # grey
}
# ntfy priority + tags (emoji shortcodes) per event.
_EVENT_NTFY: Dict[NotificationEventType, tuple[str, str]] = {
    NotificationEventType.PRINT_COMPLETED: ("default", "white_check_mark"),
    NotificationEventType.PRINT_FAILED: ("high", "x"),
    NotificationEventType.PRINT_CANCELLED: ("default", "warning"),
    NotificationEventType.PRINTER_OFFLINE: ("high", "satellite"),
}
# A leading glyph per event for the channels that render inline emoji
# (Telegram, Discord). ntfy uses its own Tags header instead, so it is omitted.
_EVENT_EMOJI: Dict[NotificationEventType, str] = {
    NotificationEventType.PRINT_COMPLETED: "✅",
    NotificationEventType.PRINT_FAILED: "❌",
    NotificationEventType.PRINT_CANCELLED: "⚠️",
    NotificationEventType.PRINTER_OFFLINE: "📡",
}


def _event_emoji(context: Dict[str, Any]) -> str:
    return _EVENT_EMOJI.get(_event_type(context), "🔔")


def _model_url(context: Dict[str, Any]) -> Optional[str]:
    """The model's source page, but only if it is a safe http(s) link to embed.

    Renderers turn this into a clickable "View model" link / tap target. We
    only trust ``http(s)`` so a stored ``javascript:``/``data:`` value can never
    become a notification action.
    """
    url = context.get("model_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def _event_type(context: Dict[str, Any]) -> NotificationEventType:
    return NotificationEventType(context["event"])


def event_label(context: Dict[str, Any]) -> str:
    return _EVENT_LABELS.get(_event_type(context), context.get("event", "Event"))


def _fmt_duration(seconds: Optional[int]) -> Optional[str]:
    if not seconds or seconds <= 0:
        return None
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def summary_lines(context: Dict[str, Any]) -> list[str]:
    """Human-readable detail lines shared across plaintext-ish targets."""
    lines: list[str] = []
    printer = context.get("printer_name") or context.get("printer_id")
    if printer:
        lines.append(f"Printer: {printer}")
    if context.get("model_name"):
        lines.append(f"Model: {context['model_name']}")
    if context.get("filename"):
        lines.append(f"File: {context['filename']}")
    dur = _fmt_duration(context.get("duration_s"))
    if dur:
        lines.append(f"Duration: {dur}")
    if context.get("filament_used_g"):
        lines.append(f"Filament: {round(float(context['filament_used_g']), 1)} g")
    if context.get("error"):
        lines.append(f"Error: {context['error']}")
    return lines


def _title(context: Dict[str, Any]) -> str:
    label = event_label(context)
    printer = context.get("printer_name")
    return f"{label} — {printer}" if printer else label


def _body_text(context: Dict[str, Any]) -> str:
    lines = summary_lines(context)
    return "\n".join([_title(context), *lines]) if lines else _title(context)


def _require(config: Dict[str, Any], key: str, target: str) -> str:
    value = config.get(key)
    if not value or not str(value).strip():
        raise RenderError(f"{target} channel is missing required config '{key}'")
    return str(value).strip()


def render_webhook(context: Dict[str, Any], config: Dict[str, Any]) -> OutboundRequest:
    """Generic webhook: POST the event context as JSON, optionally HMAC-signed.

    The body is serialised here (sent as raw ``data``, not ``json``) so that —
    when a ``secret`` is configured — the ``X-PrintStash-Signature`` header is an
    HMAC-SHA256 over the *exact bytes* transmitted, which the receiver can
    recompute to verify authenticity.
    """
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


def render_discord(context: Dict[str, Any], config: Dict[str, Any]) -> OutboundRequest:
    """Discord webhook: a single embed with the event detail as fields."""
    url = _require(config, "url", "discord")
    et = _event_type(context)
    fields = [
        {"name": part.split(": ", 1)[0], "value": part.split(": ", 1)[1], "inline": True}
        for part in summary_lines(context)
        if ": " in part
    ]
    embed: Dict[str, Any] = {
        "title": f"{_event_emoji(context)} {_title(context)}",
        "color": _EVENT_COLORS.get(et, 0x95A5A6),
        "footer": {"text": "PrintStash"},
    }
    # A title ``url`` makes the embed heading itself a clickable link to the
    # model's source page — Discord's idiomatic "view more" affordance.
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


def render_telegram(context: Dict[str, Any], config: Dict[str, Any]) -> OutboundRequest:
    """Telegram Bot API sendMessage with an HTML-formatted body.

    Uses ``parse_mode=HTML`` with every interpolated value HTML-escaped. HTML is
    chosen over Markdown deliberately: legacy Markdown has no reliable escaping,
    so a perfectly ordinary filename like ``benchy_v2.gcode`` (one ``_``) makes
    the message unparseable and Telegram rejects it with HTTP 400. HTML escaping
    (``&`` ``<`` ``>``) is unambiguous, so arbitrary printer/model/file names are
    always safe.
    """
    token = _require(config, "bot_token", "telegram")
    chat_id = _require(config, "chat_id", "telegram")
    text = f"{_event_emoji(context)} <b>{html.escape(_title(context))}</b>"
    lines = summary_lines(context)
    if lines:
        text += "\n" + "\n".join(html.escape(line) for line in lines)
    model_url = _model_url(context)
    if model_url:
        # quote=True also escapes the quotes that delimit the href attribute.
        text += f'\n🔗 <a href="{html.escape(model_url, quote=True)}">View model</a>'
    return OutboundRequest(
        method="POST",
        url=f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        headers={"Content-Type": "application/json"},
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )


def _header_safe(value: str) -> str:
    """Make a string safe to transmit as an HTTP header value.

    HTTP headers are latin-1 at best; httpx encodes them as ASCII and raises on
    anything outside it. Our own titles contain an em-dash (``—``) and user
    printer/model names can contain any Unicode, so a raw value breaks *every*
    ntfy send. Non-latin-1 values are RFC 2047-encoded (``=?utf-8?…?=``), which
    ntfy decodes for display.
    """
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def render_ntfy(context: Dict[str, Any], config: Dict[str, Any]) -> OutboundRequest:
    """ntfy: POST the body to ``{server}/{topic}`` with Title/Priority/Tags headers."""
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
    # Make the notification open the model page on tap, plus an explicit button.
    # ntfy's Click/Actions headers must stay a literal ASCII URL (RFC 2047
    # encoding would break them), so only attach when the URL is header-safe.
    model_url = _model_url(context)
    if model_url:
        try:
            model_url.encode("latin-1")
            headers["Click"] = model_url
            headers["Actions"] = f"view, View model, {model_url}"
        except UnicodeEncodeError:
            pass
    body = "\n".join(summary_lines(context)) or event_label(context)
    return OutboundRequest(method="POST", url=f"{server}/{topic}", headers=headers, data=body)


RENDERERS: Dict[NotificationTarget, Callable[[Dict[str, Any], Dict[str, Any]], OutboundRequest]] = {
    NotificationTarget.WEBHOOK: render_webhook,
    NotificationTarget.DISCORD: render_discord,
    NotificationTarget.TELEGRAM: render_telegram,
    NotificationTarget.NTFY: render_ntfy,
}


def render(
    target: NotificationTarget, context: Dict[str, Any], config: Dict[str, Any]
) -> OutboundRequest:
    """Render ``context`` for ``target`` using its channel ``config``."""
    try:
        renderer = RENDERERS[target]
    except KeyError:  # pragma: no cover - guarded by enum
        raise RenderError(f"no renderer registered for target {target}")
    return renderer(context, config)
