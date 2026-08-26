"""Standalone coverage for framework-neutral notification rendering."""

from __future__ import annotations

import hashlib
import hmac
import json
from email.header import decode_header, make_header
from typing import Any

import pytest
from printstash_core.notifications import (
    RENDERERS,
    NotificationEventType,
    NotificationTarget,
    RenderError,
    render,
    render_discord,
    render_ntfy,
    render_telegram,
    render_webhook,
    summary_lines,
)


def _context(**overrides: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
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
    context.update(overrides)
    return context


def _decoded_header(value: str) -> str:
    return str(make_header(decode_header(value)))


def test_neutral_contract_enums_cover_the_rendering_vocabulary() -> None:
    assert {event.value for event in NotificationEventType} == {
        "print_completed",
        "print_failed",
        "print_cancelled",
        "printer_offline",
    }
    assert {target.value for target in NotificationTarget} == {
        "webhook",
        "discord",
        "telegram",
        "ntfy",
    }
    assert set(RENDERERS) == set(NotificationTarget)


def test_webhook_wraps_and_signs_the_exact_transmitted_body() -> None:
    secret = "s3cr3t"

    request = render_webhook(
        _context(),
        {"url": "https://example.com/hook", "secret": secret},
    )

    assert request.method == "POST"
    assert request.url == "https://example.com/hook"
    assert request.json is None
    assert request.data is not None
    payload = json.loads(request.data)
    assert payload["event"] == "print_completed"
    assert payload["data"]["filename"] == "benchy.gcode"
    expected = hmac.new(
        secret.encode(),
        request.data.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert request.headers["X-PrintStash-Signature"] == f"sha256={expected}"


def test_discord_renders_event_style_fields_and_model_link() -> None:
    request = render_discord(
        _context(),
        {"url": "https://discord.com/api/webhooks/x/y"},
    )

    embed = request.json["embeds"][0]
    assert embed["title"] == "✅ Print completed — Ender 3"
    assert embed["color"] == 0x2ECC71
    assert embed["url"] == "https://www.printables.com/model/123-benchy"
    assert embed["timestamp"] == "2026-06-21T10:00:00Z"
    assert {field["name"] for field in embed["fields"]} >= {
        "Printer",
        "Model",
        "File",
        "Duration",
        "Filament",
    }


def test_discord_failed_event_is_red_and_unsafe_link_is_omitted() -> None:
    request = render_discord(
        _context(event="print_failed", model_url="javascript:alert(1)"),
        {"url": "https://discord.example/hook"},
    )

    embed = request.json["embeds"][0]
    assert embed["color"] == 0xE74C3C
    assert "url" not in embed


def test_telegram_escapes_html_and_supports_an_explicit_api_base() -> None:
    request = render_telegram(
        _context(filename="a_b<c&d.gcode"),
        {"bot_token": "123:ABC", "chat_id": "-100"},
        api_base="http://telegram.test",
    )

    assert request.url == "http://telegram.test/bot123:ABC/sendMessage"
    assert request.json["chat_id"] == "-100"
    assert request.json["parse_mode"] == "HTML"
    assert "a_b&lt;c&amp;d.gcode" in request.json["text"]
    assert (
        '<a href="https://www.printables.com/model/123-benchy">View model</a>'
        in request.json["text"]
    )


def test_telegram_never_embeds_non_http_model_urls() -> None:
    request = render_telegram(
        _context(model_url="data:text/html,bad"),
        {"bot_token": "t", "chat_id": "c"},
    )

    assert "<a href" not in request.json["text"]


def test_ntfy_builds_safe_headers_auth_body_and_actions() -> None:
    request = render_ntfy(
        _context(event="print_failed", printer_name="Impresora-Ñ"),
        {
            "topic": "my3d",
            "server_url": "https://push.example/",
            "token": "tk_abc",
        },
    )

    assert request.url == "https://push.example/my3d"
    assert request.headers["Title"].encode("latin-1")
    assert _decoded_header(request.headers["Title"]) == "Print failed — Impresora-Ñ"
    assert request.headers["Priority"] == "high"
    assert request.headers["Authorization"] == "Bearer tk_abc"
    assert request.headers["Click"] == _context()["model_url"]
    assert request.data and "Printer: Impresora-Ñ" in request.data


def test_summary_omits_absent_optional_values() -> None:
    lines = summary_lines(
        _context(model_name=None, filament_used_g=None, duration_s=None)
    )

    assert "File: benchy.gcode" in lines
    assert all(
        not line.startswith(("Model:", "Filament:", "Duration:")) for line in lines
    )


@pytest.mark.parametrize(
    ("target", "config"),
    [
        (NotificationTarget.WEBHOOK, {}),
        (NotificationTarget.DISCORD, {}),
        (NotificationTarget.TELEGRAM, {"bot_token": "t"}),
        (NotificationTarget.NTFY, {}),
    ],
)
def test_missing_required_config_raises(
    target: NotificationTarget,
    config: dict[str, Any],
) -> None:
    with pytest.raises(RenderError):
        render(target, _context(), config)
