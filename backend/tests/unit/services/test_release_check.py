"""Telling a self-hoster about an update, and never breaking because GitHub is down.

The update check is a convenience that talks to a third party, which makes
*degrading well* its most important property. Every failure mode returns
"unavailable" rather than raising: GitHub unreachable, a non-dict payload, a
release with no tag. A settings page that fails to load because an external API
changed shape is a much worse outcome than one that cannot report a version.

The comparison rows are semantic rather than lexical, because `0.10.0` is newer
than `0.9.0` and a string comparison says otherwise — which would either hide a
real update or nag about a downgrade.
"""

from __future__ import annotations

import asyncio

import httpx

from app.services import release_check
from app.services.release_check import (
    GITHUB_LATEST_RELEASE_URL,
    _fetch_release_status,
    is_newer_release,
)


def _stub_fetch(monkeypatch) -> dict[str, int]:
    """Replace the upstream fetch with a counter, and start from an empty cache.

    Emptying `_cache` is the load-bearing half: it is module state, so a previous
    test's entry would make the first call here a cache hit and both of the tests
    below would pass without exercising anything.
    """
    calls = {"n": 0}

    async def _fake_fetch(current_version: str, **_kwargs):
        calls["n"] += 1
        return {"status": "up_to_date", "current_version": current_version}

    monkeypatch.setattr(release_check, "_fetch_release_status", _fake_fetch)
    monkeypatch.setattr(release_check, "_cache", {})
    return calls


class TestFetchReleaseStatus:
    def test_fetch_release_status_reports_available_update(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == GITHUB_LATEST_RELEASE_URL
            return httpx.Response(
                200,
                json={
                    "tag_name": "v0.10.1",
                    "html_url": "https://github.com/xiao-villamor/PrintStash/releases/tag/v0.10.1",
                    "published_at": "2026-07-14T10:00:00Z",
                },
            )

        result = asyncio.run(
            _fetch_release_status("0.10.0", transport=httpx.MockTransport(handler))
        )

        assert result["status"] == "update_available"
        assert result["current_version"] == "0.10.0"
        assert result["latest_version"] == "0.10.1"
        assert result["update_available"] is True

    def test_fetch_release_status_degrades_when_github_is_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        result = asyncio.run(
            _fetch_release_status("0.10.0", transport=httpx.MockTransport(handler))
        )

        assert result["status"] == "unavailable"
        assert result["update_available"] is False
        assert result["latest_version"] is None

    def test_fetch_release_status_unavailable_for_non_dict_payload(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "a", "dict"])

        result = asyncio.run(
            _fetch_release_status("0.10.0", transport=httpx.MockTransport(handler))
        )
        assert result["status"] == "unavailable"

    def test_fetch_release_status_unavailable_for_missing_tag(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"html_url": "https://example.com"})

        result = asyncio.run(
            _fetch_release_status("0.10.0", transport=httpx.MockTransport(handler))
        )
        assert result["status"] == "unavailable"
        assert result["latest_version"] is None


class TestGetReleaseStatus:
    def test_get_release_status_serves_a_second_call_from_cache(
        self, monkeypatch
    ) -> None:
        calls = _stub_fetch(monkeypatch)

        asyncio.run(release_check.get_release_status("0.10.0"))
        asyncio.run(release_check.get_release_status("0.10.0"))

        assert calls["n"] == 1

    def test_get_release_status_re_fetches_when_forced(self, monkeypatch) -> None:
        calls = _stub_fetch(monkeypatch)
        asyncio.run(release_check.get_release_status("0.10.0"))

        forced = asyncio.run(release_check.get_release_status("0.10.0", force=True))

        assert forced["status"] == "up_to_date"
        assert calls["n"] == 2

    def test_release_versions_compare_semantically(self) -> None:
        assert is_newer_release("0.10.1", "0.10.0") is True
        assert is_newer_release("v1.0.0", "0.10.9") is True
        assert is_newer_release("0.9.9", "0.10.0") is False
        assert is_newer_release("not-a-version", "0.10.0") is False
