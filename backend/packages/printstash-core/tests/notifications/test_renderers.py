"""Rendering a print event into one outbound request per notification channel.

These renderers are the last thing PrintStash controls before text it did not
write leaves the host. A filename, a model name, and an error string all come
from a slicer, a provider, or a user, and each channel interprets them
differently: Discord renders embed fields, Telegram parses HTML, ntfy puts the
title in an HTTP *header*. So the same untrusted value has to be neutralised
three different ways, and each way is a row here.

Three specific harms:

- **Injection into the channel's markup.** Telegram is told `parse_mode: HTML`,
  so an unescaped `<` in a filename is markup. It is escaped, and the model link
  is quote-escaped separately because it lands in an attribute.
- **A link that is not a link.** `model_url` is embedded as a clickable target in
  three channels. Anything that is not `http://` or `https://` is dropped rather
  than emitted, so a `javascript:` or `data:` URL can never become something a
  reader taps.
- **Header smuggling.** ntfy's `Title` and `Tags` are HTTP headers, and a printer
  named with a non-Latin-1 character cannot go in one raw. It is RFC 2047
  encoded; a URL that cannot be encoded at all is simply left out rather than
  breaking the request.

The other contract is the **webhook signature**. It is computed over the exact
bytes transmitted, not over a re-serialization — a receiver that recomputes it
from a re-encoded body would see a mismatch, so the signed string and the sent
string must be the same object.

Everything here is pure: a context and a config in, one `OutboundRequest` out.
Transport lives above this layer, which is why a missing config key raises at
render time rather than failing as a bad request later.
"""

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
    OutboundRequest,
    RenderError,
    event_label,
    render,
    render_discord,
    render_ntfy,
    render_telegram,
    render_webhook,
    summary_lines,
)
from printstash_core.notifications.renderers import TELEGRAM_API_BASE, _fmt_duration

MODEL_URL = "https://www.printables.com/model/123-benchy"
# Obviously fake channel credentials.
WEBHOOK_SECRET = "not-a-real-secret"
BOT_TOKEN = "123:ABC"
NTFY_TOKEN = "not-a-real-token"


def _context(**overrides: Any) -> dict[str, Any]:
    """A completed print with every optional detail present."""

    context: dict[str, Any] = {
        "event": "print_completed",
        "printer_id": 3,
        "printer_name": "Ender 3",
        "filename": "benchy.gcode",
        "model_name": "3DBenchy",
        "model_url": MODEL_URL,
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


class TestNotificationEventType:
    def test_names_every_event_a_notification_can_report(self) -> None:
        # These strings are stored on notification-channel rows as the events a
        # user subscribed to, so renaming one silently unsubscribes them.
        assert {event.value for event in NotificationEventType} == {
            "print_completed",
            "print_failed",
            "print_cancelled",
            "printer_offline",
        }


class TestNotificationTarget:
    def test_names_every_channel_that_can_be_configured(self) -> None:
        assert {target.value for target in NotificationTarget} == {
            "webhook",
            "discord",
            "telegram",
            "ntfy",
        }

    def test_has_a_renderer_for_every_target(self) -> None:
        # A target with no renderer would be configurable in the UI and then
        # fail at send time, once, for that user only.
        assert set(RENDERERS) == set(NotificationTarget)


class TestOutboundRequest:
    def test_carries_no_body_by_default(self) -> None:
        request = OutboundRequest(method="POST", url="https://example.test/hook")

        # `data` and `json` are mutually exclusive at the transport; defaulting
        # both to absent keeps a renderer from accidentally setting both.
        assert request.data is None
        assert request.json is None


class TestEventLabel:
    @pytest.mark.parametrize(
        ("event", "label"),
        [
            ("print_completed", "Print completed"),
            ("print_failed", "Print failed"),
            ("print_cancelled", "Print cancelled"),
            ("printer_offline", "Printer offline"),
        ],
    )
    def test_names_every_event_in_human_words(self, event: str, label: str) -> None:
        assert event_label(_context(event=event)) == label


class TestFmtDuration:
    @pytest.mark.parametrize(
        ("seconds", "rendered"),
        [
            (3661, "1h 1m"),
            (7200, "2h 0m"),
            (61, "1m 1s"),
            (45, "45s"),
        ],
    )
    def test_renders_a_duration_at_the_precision_a_reader_wants(
        self, seconds: int, rendered: str
    ) -> None:
        # Hours drop the seconds: nobody reads "2h 0m 3s" off a phone
        # notification.
        assert _fmt_duration(seconds) == rendered

    @pytest.mark.parametrize("seconds", [0, None, -5])
    def test_says_it_cannot_render_a_duration_that_is_not_one(
        self, seconds: int | None
    ) -> None:
        # A zero or negative estimate would render as "0s", which reads as a
        # print that took no time.
        assert _fmt_duration(seconds) is None


class TestSummaryLines:
    def test_reports_every_detail_the_context_carries(self) -> None:
        assert summary_lines(_context()) == [
            "Printer: Ender 3",
            "Model: 3DBenchy",
            "File: benchy.gcode",
            "Duration: 1h 1m",
            "Filament: 12.3 g",
        ]

    def test_omits_a_detail_the_context_does_not_carry(self) -> None:
        lines = summary_lines(
            _context(model_name=None, filament_used_g=None, duration_s=None)
        )

        # An empty "Model:" line is worse than no line: it reads as a print of
        # something nameless.
        assert lines == ["Printer: Ender 3", "File: benchy.gcode"]

    def test_falls_back_to_the_printer_id_when_it_has_no_name(self) -> None:
        assert "Printer: 3" in summary_lines(_context(printer_name=None))

    def test_reports_the_failure_reason_on_a_failed_print(self) -> None:
        lines = summary_lines(_context(event="print_failed", error="Thermal runaway"))

        # The reason is the only actionable part of a failure notification.
        assert "Error: Thermal runaway" in lines

    def test_rounds_filament_use_to_a_tenth_of_a_gram(self) -> None:
        # A spool is measured to the gram; ten decimal places of float noise in
        # a push notification is just noise.
        assert "Filament: 12.3 g" in summary_lines(_context(filament_used_g=12.34))

    def test_reports_nothing_for_a_context_with_only_an_event(self) -> None:
        assert summary_lines({"event": "printer_offline"}) == []


class TestRenderWebhook:
    def test_posts_the_whole_context_as_json(self) -> None:
        request = render_webhook(_context(), {"url": "https://example.test/hook"})

        assert request.method == "POST"
        assert request.url == "https://example.test/hook"
        assert request.headers["Content-Type"] == "application/json"
        payload = json.loads(request.data or "")
        assert payload["event"] == "print_completed"
        assert payload["data"]["filename"] == "benchy.gcode"

    def test_signs_the_exact_bytes_it_transmits(self) -> None:
        request = render_webhook(
            _context(),
            {"url": "https://example.test/hook", "secret": WEBHOOK_SECRET},
        )

        # Signed over `request.data`, not over a re-serialization: a receiver
        # recomputing the digest from a re-encoded body would see a mismatch,
        # which is why the body is a pre-rendered string rather than `json=`.
        expected = hmac.new(
            WEBHOOK_SECRET.encode(), (request.data or "").encode(), hashlib.sha256
        ).hexdigest()
        assert request.headers["X-PrintStash-Signature"] == f"sha256={expected}"
        assert request.json is None

    def test_omits_the_signature_when_no_secret_is_configured(self) -> None:
        request = render_webhook(_context(), {"url": "https://example.test/hook"})

        # A signature header with an empty digest would look like a valid
        # signature to a receiver that only checks for the header's presence.
        assert "X-PrintStash-Signature" not in request.headers

    def test_serializes_the_body_deterministically(self) -> None:
        first = render_webhook(_context(), {"url": "https://example.test/hook"})
        second = render_webhook(_context(), {"url": "https://example.test/hook"})

        # Sorted keys and no whitespace: the signature is over these bytes, so
        # dict ordering must not change the digest between two sends.
        assert first.data == second.data

    def test_refuses_a_channel_with_no_url(self) -> None:
        with pytest.raises(RenderError):
            render_webhook(_context(), {})


class TestRenderDiscord:
    def test_posts_one_embed_titled_with_the_event(self) -> None:
        request = render_discord(_context(), {"url": "https://discord.test/hook"})

        embed = (request.json or {})["embeds"][0]
        assert embed["title"] == "✅ Print completed — Ender 3"
        assert embed["footer"] == {"text": "PrintStash"}

    @pytest.mark.parametrize(
        ("event", "colour"),
        [
            ("print_completed", 0x2ECC71),
            ("print_failed", 0xE74C3C),
            ("print_cancelled", 0xF39C12),
            ("printer_offline", 0x95A5A6),
        ],
    )
    def test_colours_the_embed_by_outcome(self, event: str, colour: int) -> None:
        request = render_discord(
            _context(event=event), {"url": "https://discord.test/hook"}
        )

        # The colour is how a failure is recognised in a busy channel before
        # anything is read.
        assert (request.json or {})["embeds"][0]["color"] == colour

    def test_splits_each_detail_into_its_own_inline_field(self) -> None:
        request = render_discord(_context(), {"url": "https://discord.test/hook"})

        fields = (request.json or {})["embeds"][0]["fields"]
        assert {field["name"] for field in fields} == {
            "Printer",
            "Model",
            "File",
            "Duration",
            "Filament",
        }
        assert all(field["inline"] for field in fields)

    def test_links_the_embed_to_the_model(self) -> None:
        request = render_discord(_context(), {"url": "https://discord.test/hook"})

        assert (request.json or {})["embeds"][0]["url"] == MODEL_URL

    @pytest.mark.parametrize(
        "model_url", ["javascript:alert(1)", "data:text/html,bad", "/relative", 42]
    )
    def test_omits_a_link_that_is_not_an_http_url(self, model_url: object) -> None:
        request = render_discord(
            _context(model_url=model_url), {"url": "https://discord.test/hook"}
        )

        # The embed title becomes a clickable link, so a non-HTTP scheme here is
        # a tappable payload in someone's chat client.
        assert "url" not in (request.json or {})["embeds"][0]

    def test_carries_the_events_timestamp(self) -> None:
        request = render_discord(_context(), {"url": "https://discord.test/hook"})

        assert (request.json or {})["embeds"][0]["timestamp"] == "2026-06-21T10:00:00Z"

    def test_omits_the_timestamp_when_the_event_has_none(self) -> None:
        request = render_discord(
            _context(timestamp=None), {"url": "https://discord.test/hook"}
        )

        # Discord renders a missing timestamp as absent; an empty one as invalid.
        assert "timestamp" not in (request.json or {})["embeds"][0]

    def test_omits_the_field_list_when_there_are_no_details(self) -> None:
        request = render_discord(
            {"event": "printer_offline"}, {"url": "https://discord.test/hook"}
        )

        assert "fields" not in (request.json or {})["embeds"][0]

    def test_refuses_a_channel_with_no_url(self) -> None:
        with pytest.raises(RenderError):
            render_discord(_context(), {})


class TestRenderTelegram:
    def test_posts_a_send_message_call_for_the_configured_chat(self) -> None:
        request = render_telegram(
            _context(), {"bot_token": BOT_TOKEN, "chat_id": "-100"}
        )

        assert request.url == f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"
        assert (request.json or {})["chat_id"] == "-100"

    def test_routes_through_an_explicit_api_base(self) -> None:
        request = render_telegram(
            _context(),
            {"bot_token": BOT_TOKEN, "chat_id": "-100"},
            api_base="http://telegram.test",
        )

        # Configurable so a self-hoster can route Telegram traffic through an
        # egress proxy, and so the contract tests can point at a fake Bot API.
        assert request.url == f"http://telegram.test/bot{BOT_TOKEN}/sendMessage"

    def test_declares_html_parsing(self) -> None:
        request = render_telegram(_context(), {"bot_token": "t", "chat_id": "c"})

        assert (request.json or {})["parse_mode"] == "HTML"

    def test_escapes_markup_in_a_filename(self) -> None:
        request = render_telegram(
            _context(filename="a_b<c&d.gcode"), {"bot_token": "t", "chat_id": "c"}
        )

        # `parse_mode: HTML` is declared, so an unescaped `<` from a slicer's
        # output would be markup — or would break the message entirely.
        assert "a_b&lt;c&amp;d.gcode" in (request.json or {})["text"]

    def test_links_to_the_model_with_the_url_quote_escaped(self) -> None:
        request = render_telegram(_context(), {"bot_token": "t", "chat_id": "c"})

        assert f'<a href="{MODEL_URL}">View model</a>' in (request.json or {})["text"]

    @pytest.mark.parametrize(
        "model_url", ["data:text/html,bad", "javascript:alert(1)", None]
    )
    def test_omits_a_link_that_is_not_an_http_url(self, model_url: object) -> None:
        request = render_telegram(
            _context(model_url=model_url), {"bot_token": "t", "chat_id": "c"}
        )

        assert "<a href" not in (request.json or {})["text"]

    def test_sends_only_a_title_when_there_are_no_details(self) -> None:
        request = render_telegram(
            {"event": "printer_offline"}, {"bot_token": "t", "chat_id": "c"}
        )

        assert (request.json or {})["text"] == "📡 <b>Printer offline</b>"

    def test_refuses_a_channel_with_no_bot_token(self) -> None:
        with pytest.raises(RenderError):
            render_telegram(_context(), {"chat_id": "c"})

    def test_refuses_a_channel_with_no_chat_id(self) -> None:
        with pytest.raises(RenderError):
            render_telegram(_context(), {"bot_token": "t"})


class TestRenderNtfy:
    def test_posts_the_summary_to_the_configured_topic(self) -> None:
        request = render_ntfy(_context(), {"topic": "my3d"})

        assert request.url == "https://ntfy.sh/my3d"
        assert request.data is not None
        assert "Printer: Ender 3" in request.data

    def test_posts_to_a_self_hosted_server(self) -> None:
        request = render_ntfy(
            _context(), {"topic": "my3d", "server_url": "https://push.example/"}
        )

        # The trailing slash is the shape a user pastes; keeping it would produce
        # a double separator.
        assert request.url == "https://push.example/my3d"

    @pytest.mark.parametrize(
        ("event", "priority", "tags"),
        [
            ("print_completed", "default", "white_check_mark"),
            ("print_failed", "high", "x"),
            ("print_cancelled", "default", "warning"),
            ("printer_offline", "high", "satellite"),
        ],
    )
    def test_raises_the_priority_for_an_outcome_that_needs_attention(
        self, event: str, priority: str, tags: str
    ) -> None:
        request = render_ntfy(_context(event=event), {"topic": "my3d"})

        # Priority is what decides whether a phone rings while its owner is
        # asleep; a completed print must not, a failure must.
        assert request.headers["Priority"] == priority
        assert request.headers["Tags"] == tags

    def test_encodes_a_title_that_cannot_go_in_a_raw_header(self) -> None:
        request = render_ntfy(_context(printer_name="Impresora-Ñ"), {"topic": "my3d"})

        # HTTP headers are Latin-1. A printer named with any other character has
        # to be RFC 2047 encoded, or the request is malformed and every
        # notification from that printer silently fails.
        assert request.headers["Title"].encode("latin-1")
        assert _decoded_header(request.headers["Title"]) == (
            "Print completed — Impresora-Ñ"
        )

    def test_authenticates_with_a_configured_token(self) -> None:
        request = render_ntfy(_context(), {"topic": "my3d", "token": NTFY_TOKEN})

        assert request.headers["Authorization"] == f"Bearer {NTFY_TOKEN}"

    def test_omits_the_authorization_header_for_a_public_topic(self) -> None:
        request = render_ntfy(_context(), {"topic": "my3d"})

        assert "Authorization" not in request.headers

    def test_makes_the_notification_tap_through_to_the_model(self) -> None:
        request = render_ntfy(_context(), {"topic": "my3d"})

        assert request.headers["Click"] == MODEL_URL
        assert request.headers["Actions"] == f"view, View model, {MODEL_URL}"

    def test_omits_the_tap_action_for_a_link_that_is_not_an_http_url(self) -> None:
        request = render_ntfy(
            _context(model_url="javascript:alert(1)"), {"topic": "my3d"}
        )

        assert "Click" not in request.headers

    def test_omits_the_tap_action_for_a_url_that_cannot_go_in_a_header(self) -> None:
        request = render_ntfy(
            _context(model_url="https://example.test/模型"), {"topic": "my3d"}
        )

        # A URL cannot be RFC 2047 encoded and stay clickable, so the action is
        # dropped rather than sent broken — the notification itself still
        # arrives.
        assert "Click" not in request.headers
        assert "Actions" not in request.headers

    def test_falls_back_to_the_event_label_when_there_are_no_details(self) -> None:
        request = render_ntfy({"event": "printer_offline"}, {"topic": "my3d"})

        # ntfy renders an empty body as a blank notification.
        assert request.data == "Printer offline"

    def test_declares_utf8_for_the_body(self) -> None:
        request = render_ntfy(_context(), {"topic": "my3d"})

        # The body is not header-constrained, so it carries the real characters.
        assert request.headers["Content-Type"] == "text/plain; charset=utf-8"

    def test_refuses_a_channel_with_no_topic(self) -> None:
        with pytest.raises(RenderError):
            render_ntfy(_context(), {})


class TestRender:
    @pytest.mark.parametrize("target", list(NotificationTarget))
    def test_dispatches_to_the_renderer_for_the_target(
        self, target: NotificationTarget
    ) -> None:
        config = {
            NotificationTarget.WEBHOOK: {"url": "https://example.test/hook"},
            NotificationTarget.DISCORD: {"url": "https://discord.test/hook"},
            NotificationTarget.TELEGRAM: {"bot_token": "t", "chat_id": "c"},
            NotificationTarget.NTFY: {"topic": "my3d"},
        }[target]

        request = render(target, _context(), config)

        assert request.method == "POST"

    @pytest.mark.parametrize(
        ("target", "config"),
        [
            (NotificationTarget.WEBHOOK, {}),
            (NotificationTarget.DISCORD, {}),
            (NotificationTarget.TELEGRAM, {"bot_token": "t"}),
            (NotificationTarget.NTFY, {}),
        ],
    )
    def test_refuses_a_channel_missing_a_required_setting(
        self, target: NotificationTarget, config: dict[str, Any]
    ) -> None:
        # Raised at render time, before any transport: an incomplete channel is
        # a configuration error, not a delivery failure to retry.
        with pytest.raises(RenderError):
            render(target, _context(), config)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_treats_a_blank_setting_as_missing(self, value: str) -> None:
        with pytest.raises(RenderError):
            render(NotificationTarget.NTFY, _context(), {"topic": value})
