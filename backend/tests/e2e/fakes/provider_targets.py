"""Contract-enforcing fake endpoints for the four notification targets.

These are *not* dumb 200-returning sinks. Each one replicates enough of the real
provider's acceptance rules to fail the kind of bug that reached production:

- **Telegram** validates the message body against the declared ``parse_mode``
  exactly like the real Bot API: a ``Markdown`` message with an unbalanced ``_``
  / ``*`` (e.g. the filename ``benchy_v2.gcode``) is rejected with HTTP 400
  ``can't parse entities`` — the bug that silently broke real notifications.
- **ntfy** rejects ``Title`` header values that are not latin-1 encodable (the
  real server cannot transmit them), mirroring the failure that an emoji/accented
  printer name produced.
- **Discord** requires an ``embeds`` array or ``content``.
- **webhook** records the raw body + signature so the test can verify the HMAC.

A ``/flaky/{key}`` endpoint fails its first N calls then succeeds, to exercise
real retry over real HTTP.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .recorder import Received, Recorder

# Number of times the flaky endpoint fails before succeeding.
FLAKY_FAILURES = 2

_ALLOWED_HTML_TAGS = {"b", "/b", "i", "/i", "u", "/u", "s", "/s", "code", "/code", "pre", "/pre", "a", "/a"}
_HTML_TAG_RE = re.compile(r"<([^>]*)>")
_HTML_ENTITY_RE = re.compile(r"&(?:[a-zA-Z]+|#\d+);")


def _strip_escaped(text: str, specials: str) -> str:
    """Remove ``\\x`` escape pairs so only *active* markup chars remain."""
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in specials:
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _telegram_parse_error(text: str, parse_mode: Optional[str]) -> Optional[str]:
    """Return a Bot-API-style error string if ``text`` is invalid for ``parse_mode``.

    Faithful to the real failure modes we care about, not a full parser.
    """
    if not parse_mode:
        return None
    mode = parse_mode.lower()
    if mode == "markdown":
        # Legacy Markdown: entity delimiters must balance. No backslash escaping.
        for ch in ("_", "*", "`"):
            if text.count(ch) % 2 != 0:
                return "Bad Request: can't parse entities: can't find end of entity"
        return None
    if mode == "markdownv2":
        active = _strip_escaped(text, "_*[]()~`>#+-=|{}.!")
        for ch in ("_", "*", "`"):
            if active.count(ch) % 2 != 0:
                return "Bad Request: can't parse entities"
        return None
    if mode == "html":
        # Every '<' must open an allowed tag; every '&' must start an entity.
        for tag in _HTML_TAG_RE.findall(text):
            name = tag.split(" ", 1)[0].strip().lower()
            if name not in _ALLOWED_HTML_TAGS:
                return f"Bad Request: unsupported start tag \"{name}\""
        # Strip valid tags + entities, then any leftover '<' or '&' is unescaped.
        residue = _HTML_TAG_RE.sub("", text)
        residue = _HTML_ENTITY_RE.sub("", residue)
        if "<" in residue or "&" in residue:
            return "Bad Request: can't parse entities: unescaped '<' or '&'"
        return None
    return None


def build_provider_app(recorder: Recorder) -> Starlette:
    async def discord(request: Request) -> Response:
        body = await request.json()
        recorder.record(
            Received("discord", request.method, request.url.path, dict(request.headers), json=body)
        )
        if not (isinstance(body, dict) and (body.get("embeds") or body.get("content"))):
            return JSONResponse({"message": "Cannot send an empty message", "code": 50006}, status_code=400)
        return Response(status_code=204)

    async def telegram(request: Request) -> Response:
        body = await request.json()
        recorder.record(
            Received("telegram", request.method, request.url.path, dict(request.headers), json=body)
        )
        text = (body or {}).get("text", "") if isinstance(body, dict) else ""
        parse_mode = (body or {}).get("parse_mode") if isinstance(body, dict) else None
        err = _telegram_parse_error(text, parse_mode)
        if err:
            return JSONResponse({"ok": False, "error_code": 400, "description": err}, status_code=400)
        return JSONResponse({"ok": True, "result": {"message_id": 1}})

    async def ntfy(request: Request) -> Response:
        raw = await request.body()
        headers = dict(request.headers)
        recorder.record(
            Received("ntfy", request.method, request.url.path, headers, body=raw)
        )
        # The real server transmits Title/Tags as HTTP headers; non-latin-1 values
        # cannot be sent. (In practice httpx raises before reaching us, but enforce
        # here too so the contract is explicit.)
        for key in ("title", "tags"):
            value = headers.get(key)
            if value is not None:
                try:
                    value.encode("latin-1")
                except UnicodeEncodeError:
                    return PlainTextResponse("invalid non-ASCII header", status_code=400)
        return JSONResponse({"id": "fake", "topic": request.path_params.get("topic")})

    async def webhook(request: Request) -> Response:
        raw = await request.body()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = None
        recorder.record(
            Received("webhook", request.method, request.url.path, dict(request.headers), json=parsed, body=raw)
        )
        return Response(status_code=204)

    async def flaky(request: Request) -> Response:
        key = request.path_params["key"]
        n = recorder.bump(f"flaky:{key}")
        raw = await request.body()
        recorder.record(
            Received("flaky", request.method, request.url.path, dict(request.headers), body=raw, status_returned=200 if n > FLAKY_FAILURES else 500)
        )
        if n <= FLAKY_FAILURES:
            return PlainTextResponse("temporary failure", status_code=500)
        return Response(status_code=204)

    return Starlette(
        routes=[
            Route("/discord/webhook/{wid}/{token}", discord, methods=["POST"]),
            Route("/bot{token}/sendMessage", telegram, methods=["POST"]),
            Route("/ntfy/{topic}", ntfy, methods=["POST"]),
            Route("/webhook", webhook, methods=["POST"]),
            Route("/flaky/{key}", flaky, methods=["POST"]),
        ]
    )
