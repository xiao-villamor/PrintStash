"""Unit coverage for ``notification_renderers`` — pure event -> request mapping.

These exercise the payload shape each target produces and the config-validation
boundary, with no network involved.
"""

from __future__ import annotations

import pytest

from app.db.models import NotificationTarget
from app.services import notification_renderers as r


def _ctx(**over):
    ctx = {
        "event": "print_completed",
        "printer_id": 3,
        "printer_name": "Ender 3",
        "filename": "benchy.gcode",
        "model_name": "3DBenchy",
        "model_url": "https://www.printables.com/model/123-benchy",
        "job_id": 42,
        "duration_s": 3661,
        "filament_used_g": 12.34,
        "error": None,
        "timestamp": "2026-06-21T10:00:00Z",
    }
    ctx.update(over)
    return ctx


def test_webhook_wraps_full_context():
    import json

    req = r.render_webhook(_ctx(), {"url": "https://example.com/hook"})
    assert req.method == "POST"
    assert req.url == "https://example.com/hook"
    # Body is serialised to `data` (so signing can cover exact bytes); no json.
    assert req.json is None
    body = json.loads(req.data)
    assert body["event"] == "print_completed"
    assert body["data"]["filename"] == "benchy.gcode"
    assert "X-PrintStash-Signature" not in req.headers  # no secret configured


def test_webhook_hmac_signature_when_secret_set():
    import hashlib
    import hmac

    secret = "s3cr3t"
    req = r.render_webhook(_ctx(), {"url": "https://example.com/hook", "secret": secret})
    expected = hmac.new(
        secret.encode(), req.data.encode(), hashlib.sha256
    ).hexdigest()
    assert req.headers["X-PrintStash-Signature"] == f"sha256={expected}"


def test_discord_builds_embed_with_color_and_fields():
    req = r.render_discord(_ctx(), {"url": "https://discord.com/api/webhooks/x/y"})
    embed = req.json["embeds"][0]
    assert embed["title"] == "✅ Print completed — Ender 3"
    assert embed["color"] == 0x2ECC71  # green for completed
    names = {f["name"] for f in embed["fields"]}
    assert {"Printer", "Model", "File", "Duration", "Filament"} <= names
    assert embed["timestamp"] == "2026-06-21T10:00:00Z"
    # The model's source page makes the title a clickable link.
    assert embed["url"] == "https://www.printables.com/model/123-benchy"
    assert embed["footer"]["text"] == "PrintStash"


def test_discord_omits_url_when_no_model_link():
    req = r.render_discord(_ctx(model_url=None), {"url": "https://d/x"})
    assert "url" not in req.json["embeds"][0]


def test_discord_color_differs_for_failed():
    req = r.render_discord(_ctx(event="print_failed"), {"url": "https://d/x"})
    assert req.json["embeds"][0]["color"] == 0xE74C3C  # red


def test_telegram_targets_bot_api_with_html():
    req = r.render_telegram(_ctx(), {"bot_token": "123:ABC", "chat_id": "-100"})
    assert req.url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert req.json["chat_id"] == "-100"
    assert req.json["parse_mode"] == "HTML"
    assert "✅ <b>Print completed — Ender 3</b>" in req.json["text"]
    assert "File: benchy.gcode" in req.json["text"]
    # The model's source page is rendered as a tidy inline link.
    assert (
        '<a href="https://www.printables.com/model/123-benchy">View model</a>'
        in req.json["text"]
    )


def test_telegram_omits_link_when_no_model_url():
    req = r.render_telegram(_ctx(model_url=None), {"bot_token": "t", "chat_id": "c"})
    assert "View model" not in req.json["text"]


def test_telegram_rejects_non_http_model_url():
    # A stored javascript:/data: URL must never become a clickable link.
    req = r.render_telegram(
        _ctx(model_url="javascript:alert(1)"), {"bot_token": "t", "chat_id": "c"}
    )
    assert "<a href" not in req.json["text"]


def test_telegram_escapes_html_special_chars_in_names():
    # A filename with '_' must not break parsing (the old Markdown bug); '<' & '&'
    # must be HTML-escaped so they can't inject markup.
    req = r.render_telegram(
        _ctx(filename="a_b<c&d.gcode"), {"bot_token": "t", "chat_id": "c"}
    )
    assert req.json["parse_mode"] == "HTML"
    assert "a_b&lt;c&amp;d.gcode" in req.json["text"]
    assert "<c&d" not in req.json["text"]  # raw markup did not leak through


def _decode_header(value: str) -> str:
    from email.header import decode_header, make_header

    return str(make_header(decode_header(value)))


def test_ntfy_uses_default_server_headers_and_body():
    req = r.render_ntfy(_ctx(event="print_failed"), {"topic": "my3d"})
    assert req.url == "https://ntfy.sh/my3d"
    # The title carries an em-dash, so it is RFC 2047-encoded for the header but
    # must round-trip back to the human string.
    assert req.headers["Title"].encode("latin-1")  # transmittable
    assert _decode_header(req.headers["Title"]) == "Print failed — Ender 3"
    assert req.headers["Priority"] == "high"
    assert req.data and "Printer: Ender 3" in req.data
    assert "Authorization" not in req.headers


def test_ntfy_adds_click_and_action_for_model_url():
    req = r.render_ntfy(_ctx(), {"topic": "t"})
    url = "https://www.printables.com/model/123-benchy"
    assert req.headers["Click"] == url
    assert req.headers["Actions"] == f"view, View model, {url}"


def test_ntfy_omits_click_when_no_model_url():
    req = r.render_ntfy(_ctx(model_url=None), {"topic": "t"})
    assert "Click" not in req.headers
    assert "Actions" not in req.headers


def test_ntfy_title_with_unicode_name_is_header_safe():
    req = r.render_ntfy(_ctx(printer_name="Impresora-Ñ"), {"topic": "t"})
    # Must be latin-1 transmittable (httpx would otherwise raise) and decode back.
    req.headers["Title"].encode("latin-1")
    assert "Impresora-Ñ" in _decode_header(req.headers["Title"])


def test_ntfy_custom_server_strips_slash_and_adds_token():
    req = r.render_ntfy(
        _ctx(), {"topic": "t", "server_url": "https://push.me/", "token": "tk_abc"}
    )
    assert req.url == "https://push.me/t"
    assert req.headers["Authorization"] == "Bearer tk_abc"


def test_duration_formatting_hours_minutes():
    # 3661s -> "1h 1m"
    req = r.render_telegram(_ctx(), {"bot_token": "t", "chat_id": "c"})
    assert "Duration: 1h 1m" in req.json["text"]


def test_summary_skips_absent_optional_fields():
    lines = r.summary_lines(_ctx(model_name=None, filament_used_g=None, duration_s=None))
    joined = "\n".join(lines)
    assert "Model:" not in joined
    assert "Filament:" not in joined
    assert "Duration:" not in joined
    assert "File: benchy.gcode" in joined


@pytest.mark.parametrize("target", list(NotificationTarget))
def test_registry_covers_every_target(target):
    assert target in r.RENDERERS


@pytest.mark.parametrize(
    "target,config",
    [
        (NotificationTarget.WEBHOOK, {}),
        (NotificationTarget.DISCORD, {}),
        (NotificationTarget.TELEGRAM, {"bot_token": "t"}),  # missing chat_id
        (NotificationTarget.NTFY, {}),
    ],
)
def test_missing_required_config_raises(target, config):
    with pytest.raises(r.RenderError):
        r.render(target, _ctx(), config)
