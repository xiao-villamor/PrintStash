"""Server-side rendered fetch for Cloudflare-gated pages.

MakerWorld serves its model/collection pages behind Cloudflare's managed bot
challenge ("Verify you are human"). Plain HTTP (:mod:`app.core.http_client`)
receives the interstitial, which carries no ``__NEXT_DATA__`` payload, so
imports fail. This module renders such pages with a stealth-patched headless
Chromium (Patchright) that executes the challenge JS and obtains Cloudflare
clearance automatically, then returns the rendered HTML for the normal parsing
path in :mod:`app.services.import_resolvers` to consume.

Each fetch runs in a **fresh, throwaway browser context**. This is deliberate:
re-using a context (or a persistent on-disk profile) carries Cloudflare cookies
forward, and a stale ``__cf_bm``/``cf_clearance`` triggers a *harder* managed
challenge that headless Chromium cannot solve — so the first import succeeds in
~3s and every later one hangs the full timeout. A clean context always gets the
solvable JS challenge. We launch the Chromium *process* once (so there is no
per-fetch cold start) and create/destroy a context per request, serialised
behind a lock to bound memory.

All Patchright imports are lazy: a missing Chromium binary or a launch failure
degrades to ``None`` (which callers already treat as "could not resolve")
instead of breaking module import or the whole importer.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# A realistic desktop-Chrome UA for the throwaway contexts.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1280, "height": 800}

_playwright: Any = None
_browser: Any = None
# Serialises context creation (one render at a time) and guards launch/shutdown.
_lock = asyncio.Lock()


async def _get_browser() -> Optional[Any]:
    """Return a ready Chromium browser, launching the process once.

    Returns ``None`` if Patchright is unavailable or Chromium fails to launch,
    so the feature degrades gracefully. Callers hold ``_lock``.
    """
    global _playwright, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        logger.warning("patchright not installed; browser fetch unavailable")
        return None
    try:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=bool(settings.makerworld_browser_headless)
        )
        logger.info("headless browser launched for Cloudflare-gated fetches")
        return _browser
    except Exception as exc:  # noqa: BLE001 — browser launch boundary
        logger.warning("failed to launch headless browser: %s", exc)
        await _shutdown()
        return None


def _cookies_for(url: str, cookie_header: str) -> list[dict]:
    """Parse a ``k=v; k2=v2`` header into Playwright cookie dicts for ``url``'s host."""
    host = (urlsplit(url).hostname or "").lstrip(".")
    if not host:
        return []
    # Dot-prefix the registrable domain so the cookie spans makerworld subdomains.
    domain = "." + host.split(".", 1)[1] if host.count(".") >= 2 else host
    cookies: list[dict] = []
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if name and sep:
            cookies.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return cookies


async def fetch_rendered_html(
    url: str,
    *,
    wait_selector: Optional[str] = None,
    extra_cookie: Optional[str] = None,
) -> Optional[str]:
    """Render ``url`` in a fresh headless context and return its HTML, or ``None``.

    ``wait_selector`` is awaited (best-effort) after navigation so we return the
    page only once the data we need has hydrated (e.g. ``script#__NEXT_DATA__``).
    ``extra_cookie`` injects a caller-supplied session cookie for private pages.
    """
    if not settings.makerworld_browser_enabled:
        return None
    timeout_ms = max(5, int(settings.browser_fetch_timeout_seconds)) * 1000
    async with _lock:
        browser = await _get_browser()
        if browser is None:
            return None
        context = None
        try:
            context = await browser.new_context(
                user_agent=_USER_AGENT, locale="en-US", viewport=_VIEWPORT
            )
            if extra_cookie:
                try:
                    await context.add_cookies(_cookies_for(url, extra_cookie))
                except Exception as exc:  # noqa: BLE001 — cookie injection is best-effort
                    logger.warning("could not inject cookie for %s: %s", url, exc)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if wait_selector:
                try:
                    # state="attached": resolve as soon as the node is in the DOM.
                    # The default ("visible") never fires for <script> tags, which
                    # are not rendered — that would burn the whole timeout.
                    await page.wait_for_selector(
                        wait_selector, state="attached", timeout=timeout_ms
                    )
                except Exception:
                    # Selector never appeared (challenge stuck or page reshaped);
                    # return whatever rendered and let the caller's parser decide.
                    logger.warning("wait_selector %r not found at %s", wait_selector, url)
            return await page.content()
        except Exception as exc:  # noqa: BLE001 — navigation boundary
            logger.warning("browser fetch failed for %s: %s", url, exc)
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass


async def api_get(
    url: str,
    *,
    cookie: Optional[str] = None,
    headers: Optional[dict] = None,
) -> Optional[tuple[int, str]]:
    """GET a JSON API ``url`` from inside the browser, past Cloudflare.

    Cloudflare blocks plain httpx on MakerWorld's API the same way it blocks the
    HTML. The browser's request context carries the right TLS fingerprint, but
    only after the JS challenge has been solved — so we warm a fresh context by
    loading the site root, then issue the request. ``cookie`` (the user's login
    session) is injected so auth-gated endpoints (file downloads) succeed.

    Returns ``(status_code, body_text)`` or ``None`` if the browser is
    unavailable / the request errors.
    """
    if not settings.makerworld_browser_enabled:
        return None
    timeout_ms = max(5, int(settings.browser_fetch_timeout_seconds)) * 1000
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.hostname}"
    async with _lock:
        browser = await _get_browser()
        if browser is None:
            return None
        context = None
        try:
            context = await browser.new_context(
                user_agent=_USER_AGENT, locale="en-US", viewport=_VIEWPORT
            )
            if cookie:
                try:
                    await context.add_cookies(_cookies_for(url, cookie))
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning("could not inject cookie for %s: %s", url, exc)
            page = await context.new_page()
            # Warm up: solving the root's challenge grants clearance to the
            # context, which the request below then reuses.
            await page.goto(origin + "/", wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
            resp = await context.request.get(url, headers=headers or {}, timeout=timeout_ms)
            return resp.status, await resp.text()
        except Exception as exc:  # noqa: BLE001 — navigation/request boundary
            logger.warning("browser api_get failed for %s: %s", url, exc)
            return None
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass


async def _shutdown() -> None:
    """Tear down the browser + Playwright driver. Safe to call repeatedly."""
    global _playwright, _browser
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
    _browser = None
    _playwright = None


async def close_browser() -> None:
    """Release the headless browser at application shutdown."""
    async with _lock:
        await _shutdown()
