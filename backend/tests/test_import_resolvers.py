"""Unit coverage for ``import_resolvers`` — turning model *page* URLs into
direct download URLs.

The host HTTP calls (Printables GraphQL, MakerWorld page + API) are patched at
the module's small network helpers, so these tests exercise the dispatch, id
extraction, pack selection and JSON-walking logic without any real network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import import_resolvers as r
from app.services.importer import ImportError_


# --------------------------------------------------------------------------- #
# Host classification + id extraction (pure functions)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.printables.com/model/3161-3d-benchy", "printables"),
        ("https://www.printables.com/model/3161-3d-benchy/files", "printables"),
        ("https://printables.com/model/3161", "printables"),
        ("https://makerworld.com/en/models/1123776-original-3d-benchy", "makerworld"),
        ("https://makerworld.com/es/models/1123776?from=search#x", "makerworld"),
        ("https://www.thingiverse.com/thing:763622", "thingiverse"),
        ("https://www.thingiverse.com/thing:763622/files", "thingiverse"),
        # Direct blob URLs are not pages — different host / no model id.
        ("https://files.printables.com/abc/3dbenchy.stl", None),
        ("https://example.com/model.zip", None),
        # Known host but no extractable id -> treated as direct, not a page.
        ("https://www.printables.com/social/123-user", None),
    ],
)
def test_classify_page(url: str, expected) -> None:
    assert r.classify_page(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Regression: endswith("makerworld.com") used to classify look-alike
        # hosts as MakerWorld pages.
        "https://evilmakerworld.com/en/models/123",
        "https://makerworld.com.attacker.test/models/123",
        "https://notmakerworld.com/models/123",
    ],
)
def test_classify_page_rejects_lookalike_makerworld_hosts(url: str) -> None:
    assert r.classify_page(url) is None


def test_classify_page_accepts_makerworld_subdomain() -> None:
    assert r.classify_page("https://www.makerworld.com/en/models/123") == "makerworld"


def test_classify_page_is_case_insensitive_on_host() -> None:
    assert r.classify_page("https://WWW.PRINTABLES.COM/model/3161") == "printables"


def test_classify_page_handles_garbage_input() -> None:
    assert r.classify_page("not a url") is None
    assert r.classify_page("") is None


def test_id_extractors() -> None:
    assert (
        r._printables_id("https://www.printables.com/model/3161-3d-benchy/files")
        == "3161"
    )
    assert r._makerworld_id("https://makerworld.com/en/models/1123776-x") == "1123776"
    assert r._thingiverse_id("https://www.thingiverse.com/thing:763622") == "763622"
    assert (
        r._thingiverse_id("https://www.thingiverse.com/things/763622/files") == "763622"
    )


# --------------------------------------------------------------------------- #
# Generic JSON helpers
# --------------------------------------------------------------------------- #
def test_first_download_url_prefers_keyed_link() -> None:
    data = {"a": {"nested": {"downloadUrl": "https://cdn.test/x.zip"}}, "b": [1, 2]}
    assert r._first_download_url(data) == "https://cdn.test/x.zip"


def test_first_download_url_falls_back_to_model_like_string() -> None:
    data = {"meta": "hello", "links": ["https://cdn.test/model.3mf", "not-a-url"]}
    assert r._first_download_url(data) == "https://cdn.test/model.3mf"


def test_first_download_url_none_when_nothing_matches() -> None:
    assert (
        r._first_download_url({"meta": "hello", "n": 3, "page": "https://x.test/about"})
        is None
    )


def test_pick_printables_pack_prefers_model_files() -> None:
    packs = [{"id": 5, "fileType": "OTHER"}, {"id": 9, "fileType": "MODEL_FILES"}]
    assert r._pick_printables_pack(packs) == "9"
    # Falls back to the first pack with an id when there's no MODEL_FILES pack.
    assert r._pick_printables_pack([{"id": 7, "fileType": "GCODE"}]) == "7"
    assert r._pick_printables_pack([]) is None


def test_first_download_url_keyed_link_beats_deep_fallback() -> None:
    # A keyed url anywhere wins over a model-looking bare string.
    data = {
        "files": ["https://cdn.test/a.stl"],
        "meta": {"url": "https://cdn.test/real.zip"},
    }
    assert r._first_download_url(data) == "https://cdn.test/real.zip"


def test_first_download_url_ignores_non_http_keyed_values() -> None:
    # A relative or non-http "url" must not be returned as a download link.
    data = {"url": "/local/path.zip", "links": ["https://cdn.test/model.stl"]}
    assert r._first_download_url(data) == "https://cdn.test/model.stl"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://cdn.test/model.stl", True),
        ("https://cdn.test/model.3MF", True),  # case-insensitive ext
        ("https://cdn.test/get?file=model.stl", False),  # ext only in query
        ("https://cdn.test/api/download/123", True),  # /download path
        ("https://cdn.test/image.png", False),
        ("https://cdn.test/about", False),
    ],
)
def test_looks_like_download(url: str, expected: bool) -> None:
    assert r._looks_like_download(url) is expected


def test_extract_next_data_round_trips() -> None:
    html = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"x":1}}</script></html>'
    assert r._extract_next_data(html) == {"props": {"x": 1}}
    assert r._extract_next_data("<html>no next data</html>") is None


def test_extract_next_data_invalid_json_returns_none() -> None:
    html = '<script id="__NEXT_DATA__" type="application/json">{not json}</script>'
    assert r._extract_next_data(html) is None


def test_pick_printables_pack_non_list_returns_none() -> None:
    assert r._pick_printables_pack(None) is None
    assert r._pick_printables_pack("nope") is None


def test_printables_link_from_output_falls_back_to_files_list() -> None:
    payload = {
        "data": {
            "getDownloadLink": {
                "output": {"files": [{"link": "https://files.printables.test/a.stl"}]}
            }
        }
    }
    assert (
        r._printables_link_from_output(payload) == "https://files.printables.test/a.stl"
    )


def test_printables_links_from_output_falls_back_to_single_link() -> None:
    payload = {
        "data": {
            "getDownloadLink": {
                "output": {"link": "https://files.printables.test/x.zip"}
            }
        }
    }
    assert r._printables_links_from_output(payload) == [
        "https://files.printables.test/x.zip"
    ]
    assert r._printables_links_from_output({}) == []


@pytest.mark.asyncio
async def test_printables_graphql_raises_on_blocked_status() -> None:
    resp = MagicMock(status_code=403)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    with patch.object(r, "get_http_client", return_value=client):
        with pytest.raises(ImportError_) as exc:
            await r._printables_graphql("query", {}, "https://printables.com")
    assert str(exc.value) == "printables_blocked"


@pytest.mark.asyncio
async def test_printables_graphql_returns_json_on_success() -> None:
    resp = MagicMock(status_code=200)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"data": {}})
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    with patch.object(r, "get_http_client", return_value=client):
        out = await r._printables_graphql("query", {}, "https://printables.com")
    assert out == {"data": {}}


# --------------------------------------------------------------------------- #
# resolve_page_url dispatch
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resolve_unknown_host_returns_none() -> None:
    # No network: unknown hosts short-circuit before any resolver runs.
    assert await r.resolve_page_url("https://example.com/foo.zip") is None


@pytest.mark.asyncio
async def test_resolve_thingiverse_builds_zip_url() -> None:
    url = "https://www.thingiverse.com/thing:763622/files"
    assert (
        await r.resolve_page_url(url) == "https://www.thingiverse.com/thing:763622/zip"
    )


@pytest.mark.asyncio
async def test_resolve_printables_uses_pack_link() -> None:
    meta = {
        "data": {
            "print": {
                "id": "3161",
                "downloadPacks": [{"id": "42", "fileType": "MODEL_FILES"}],
                "stls": [],
            }
        }
    }
    link_payload = {
        "data": {
            "getDownloadLink": {
                "ok": True,
                "output": {"link": "https://files.printables.test/pack.zip"},
            }
        }
    }

    graphql = AsyncMock(side_effect=[meta, link_payload])
    with patch.object(r, "_printables_graphql", graphql):
        out = await r.resolve_page_url(
            "https://www.printables.com/model/3161-3d-benchy"
        )

    assert out == "https://files.printables.test/pack.zip"
    assert graphql.await_count == 2  # meta query, then link mutation


@pytest.mark.asyncio
async def test_resolve_printables_unresolved_raises_host_error() -> None:
    meta = {"data": {"print": {"id": "3161", "downloadPacks": [], "stls": []}}}
    with patch.object(r, "_printables_graphql", AsyncMock(return_value=meta)):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_page_url("https://www.printables.com/model/3161-3d-benchy")
    assert str(exc.value) == "printables_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_printables_network_error_becomes_host_error() -> None:
    with patch.object(
        r, "_printables_graphql", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_page_url("https://www.printables.com/model/3161-3d-benchy")
    assert str(exc.value) == "printables_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_printables_no_id_returns_none() -> None:
    assert await r._resolve_printables("https://www.printables.com/social/1-x") is None


@pytest.mark.asyncio
async def test_resolve_printables_no_print_object_returns_none() -> None:
    with patch.object(r, "_printables_graphql", AsyncMock(return_value={"data": {}})):
        assert (
            await r._resolve_printables("https://www.printables.com/model/3161-x")
            is None
        )


@pytest.mark.asyncio
async def test_resolve_printables_falls_back_to_stl_ids_when_pack_link_missing() -> (
    None
):
    meta = {
        "data": {
            "print": {
                "id": "3161",
                "downloadPacks": [{"id": "42", "fileType": "MODEL_FILES"}],
                "stls": [{"id": "7", "name": "a.stl"}],
            }
        }
    }
    # Pack mutation resolves with no usable link; stl-ids mutation succeeds.
    pack_payload = {"data": {"getDownloadLink": {"output": {}}}}
    stl_payload = {
        "data": {
            "getDownloadLink": {
                "output": {"link": "https://files.printables.test/a.stl"}
            }
        }
    }
    graphql = AsyncMock(side_effect=[meta, pack_payload, stl_payload])
    with patch.object(r, "_printables_graphql", graphql):
        out = await r.resolve_page_url(
            "https://www.printables.com/model/3161-3d-benchy"
        )
    assert out == "https://files.printables.test/a.stl"
    assert graphql.await_count == 3


@pytest.mark.asyncio
async def test_list_printables_files_no_print_id_returns_none() -> None:
    assert (
        await r._list_printables_files("https://www.printables.com/social/1-x") is None
    )


@pytest.mark.asyncio
async def test_list_printables_files_no_print_object_returns_none() -> None:
    with patch.object(r, "_printables_graphql", AsyncMock(return_value={"data": {}})):
        assert (
            await r._list_printables_files("https://www.printables.com/model/3161-x")
            is None
        )


@pytest.mark.asyncio
async def test_printables_download_links_no_files_returns_empty() -> None:
    assert (
        await r._printables_download_links(
            "https://www.printables.com/model/3161-x", []
        )
        == []
    )


@pytest.mark.asyncio
async def test_resolve_printables_collection_no_id_returns_none() -> None:
    assert (
        await r._resolve_printables_collection("https://printables.com/social/1-x")
        is None
    )


@pytest.mark.asyncio
async def test_resolve_printables_collection_skips_duplicate_and_missing_ids() -> None:
    name_payload = {"data": {"collection": {"name": "cool"}}}
    members_payload = {
        "data": {
            "moreCollectionModels": {
                "cursor": "",
                "items": [
                    {"id": "1", "print": {"id": "1", "name": "A"}},
                    {"id": "1", "print": {"id": "1", "name": "A dup"}},
                    {"print": {}},
                ],
            }
        }
    }
    graphql = AsyncMock(side_effect=[name_payload, members_payload])
    with patch.object(r, "_printables_graphql", graphql):
        _, members = await r.resolve_collection_url(
            "https://printables.com/collections/9"
        )
    assert [m.source_id for m in members] == ["1"]


@pytest.mark.asyncio
async def test_resolve_makerworld_uses_instance_download_api() -> None:
    # design API -> defaultInstanceId; instance f3mf API -> the file link.
    design = {"id": 1, "defaultInstanceId": 99}
    f3mf = {"url": "https://cdn.makerworld.test/x.3mf"}

    async def fake_api(api_url, *_a, **_k):
        return f3mf if "/instance/99/f3mf" in api_url else design

    with patch.object(r, "_makerworld_api_get", side_effect=fake_api):
        out = await r.resolve_page_url("https://makerworld.com/en/models/1123776-x")
    assert out == "https://cdn.makerworld.test/x.3mf"


@pytest.mark.asyncio
async def test_resolve_makerworld_falls_back_to_model_api() -> None:
    # No instance on the design -> fall back to the model-level download endpoint.
    bundle = {"url": "https://cdn.makerworld.test/bundle.zip"}

    async def fake_api(api_url, *_a, **_k):
        if "/instance/" in api_url:
            return None
        if "/design/" in api_url:
            return {"id": 1}  # no defaultInstanceId / instances
        return bundle  # /models/{id}/download

    with patch.object(r, "_makerworld_api_get", side_effect=fake_api):
        out = await r.resolve_page_url("https://makerworld.com/en/models/1123776-x")
    assert out == "https://cdn.makerworld.test/bundle.zip"


def test_makerworld_api_headers_includes_nonce_when_given() -> None:
    headers = r._makerworld_api_headers("https://makerworld.com/x", "abc123")
    assert headers["X-Nonce"] == "abc123"
    assert "X-Nonce" not in r._makerworld_api_headers("https://makerworld.com/x", None)


@pytest.mark.asyncio
async def test_makerworld_fetch_page_sends_cookie_header() -> None:
    good = '<script id="__NEXT_DATA__">{}</script>'
    client = _fake_http_client(200, good)
    with patch.object(r, "get_http_client", return_value=client):
        out = await r._makerworld_fetch_page(
            "https://makerworld.com/en/models/1-x", "session=abc"
        )
    assert out == good
    assert client.get.await_args.kwargs["headers"]["Cookie"] == "session=abc"


@pytest.mark.asyncio
async def test_makerworld_fetch_page_returns_original_html_when_browser_fallback_fails() -> (
    None
):
    challenge = "<title>Just a moment...</title><div class='cf-chl'></div>"
    browser = AsyncMock(return_value=None)
    with (
        patch.object(
            r, "get_http_client", return_value=_fake_http_client(200, challenge)
        ),
        patch.object(r.browser_fetch, "fetch_rendered_html", browser),
    ):
        out = await r._makerworld_fetch_page(
            "https://makerworld.com/en/models/1-x", None
        )
    assert out == challenge


@pytest.mark.asyncio
async def test_makerworld_api_get_returns_none_when_browser_fetch_returns_none() -> (
    None
):
    with patch.object(r.browser_fetch, "api_get", AsyncMock(return_value=None)):
        assert await r._makerworld_api_get("https://x", "https://x", None, None) is None


@pytest.mark.asyncio
async def test_makerworld_api_get_raises_blocked_when_not_login_related() -> None:
    with patch.object(
        r.browser_fetch, "api_get", AsyncMock(return_value=(429, "rate limited"))
    ):
        with pytest.raises(ImportError_) as exc:
            await r._makerworld_api_get("https://x", "https://x", None, None)
    assert str(exc.value) == "makerworld_blocked"


@pytest.mark.asyncio
async def test_makerworld_api_get_returns_none_on_non_200() -> None:
    with patch.object(
        r.browser_fetch, "api_get", AsyncMock(return_value=(500, "server error"))
    ):
        assert await r._makerworld_api_get("https://x", "https://x", None, None) is None


@pytest.mark.asyncio
async def test_makerworld_api_get_returns_none_on_invalid_json() -> None:
    with patch.object(
        r.browser_fetch, "api_get", AsyncMock(return_value=(200, "not json"))
    ):
        assert await r._makerworld_api_get("https://x", "https://x", None, None) is None


@pytest.mark.asyncio
async def test_resolve_makerworld_no_design_id_returns_none() -> None:
    assert (
        await r._resolve_makerworld("https://makerworld.com/en/social/1-x", None)
        is None
    )


@pytest.mark.asyncio
async def test_resolve_makerworld_finds_instance_in_instances_list() -> None:
    design = {"id": 1, "instances": [{"id": 55}]}
    f3mf = {"url": "https://cdn.makerworld.test/x.3mf"}

    async def fake_api(api_url, *_a, **_k):
        return f3mf if "/instance/55/f3mf" in api_url else design

    with patch.object(r, "_makerworld_api_get", side_effect=fake_api):
        out = await r.resolve_page_url("https://makerworld.com/en/models/1123776-x")
    assert out == "https://cdn.makerworld.test/x.3mf"


def test_makerworld_collection_members_handles_malformed_next_data() -> None:
    assert r._makerworld_collection_members({}) == []
    assert r._makerworld_collection_members({"props": None}) == []


def test_makerworld_collection_members_skips_non_dict_and_duplicate_entries() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "designs": [
                    "not-a-dict",
                    {"id": 1, "title": "A"},
                    {"id": 1, "title": "A dup"},
                ]
            }
        }
    }
    members = r._makerworld_collection_members(next_data)
    assert [m.source_id for m in members] == ["1"]


@pytest.mark.asyncio
async def test_resolve_makerworld_collection_no_id_returns_none() -> None:
    assert (
        await r._resolve_makerworld_collection(
            "https://makerworld.com/en/social/1-x", None
        )
        is None
    )


@pytest.mark.asyncio
async def test_resolve_makerworld_collection_no_html_returns_none() -> None:
    with patch.object(r, "_makerworld_fetch_page", AsyncMock(return_value=None)):
        assert (
            await r._resolve_makerworld_collection(
                "https://makerworld.com/en/collections/5-x", None
            )
            is None
        )


@pytest.mark.asyncio
async def test_resolve_makerworld_collection_no_next_data_returns_none() -> None:
    with patch.object(
        r, "_makerworld_fetch_page", AsyncMock(return_value="<html>no data</html>")
    ):
        assert (
            await r._resolve_makerworld_collection(
                "https://makerworld.com/en/collections/5-x", None
            )
            is None
        )


@pytest.mark.asyncio
async def test_resolve_makerworld_collection_falls_back_when_title_lookup_fails() -> (
    None
):
    # No ``props`` key at all -> the title KeyError is swallowed and the generic
    # "Collection <id>" title is kept; no members are found either.
    html = '<script id="__NEXT_DATA__" type="application/json">{}</script>'
    with patch.object(r, "_makerworld_fetch_page", AsyncMock(return_value=html)):
        result = await r._resolve_makerworld_collection(
            "https://makerworld.com/en/collections/5-x", None
        )
    assert result is None  # no members found from a malformed pageProps


@pytest.mark.asyncio
async def test_resolve_thingiverse_no_id_returns_none() -> None:
    assert (
        await r._resolve_thingiverse("https://www.thingiverse.com/social/x", None)
        is None
    )


@pytest.mark.asyncio
async def test_resolve_makerworld_login_required_surfaces_clear_error() -> None:
    # The auth-gated download API answers 403 "please log in" without a cookie;
    # that must surface as a specific, user-actionable error code.
    challenge = (403, '{"code":1,"error":"Please log in to download models."}')
    with patch.object(r.browser_fetch, "api_get", AsyncMock(return_value=challenge)):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_page_url("https://makerworld.com/en/models/1123776-x")
    assert str(exc.value) == "makerworld_login_required"


# --------------------------------------------------------------------------- #
# Cloudflare challenge detection + headless-browser fallback
# --------------------------------------------------------------------------- #
def test_looks_like_challenge_detects_interstitial() -> None:
    html = "<html><head><title>Just a moment...</title></head><body><div class='cf-chl'></div></body>"
    assert r._looks_like_challenge(html) is True


def test_looks_like_challenge_false_when_next_data_present() -> None:
    # A page that ships __NEXT_DATA__ is real content, even if it name-drops a marker.
    assert (
        r._looks_like_challenge('<script id="__NEXT_DATA__">{}</script> just a moment')
        is False
    )


def test_looks_like_challenge_false_for_plain_page() -> None:
    assert r._looks_like_challenge("<html><body>hello world</body></html>") is False


def _fake_http_client(status_code: int, text: str) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=status_code, text=text))
    return client


@pytest.mark.asyncio
async def test_makerworld_fetch_uses_httpx_when_not_challenged() -> None:
    good = '<script id="__NEXT_DATA__">{}</script>'
    browser = AsyncMock()
    with (
        patch.object(r, "get_http_client", return_value=_fake_http_client(200, good)),
        patch.object(r.browser_fetch, "fetch_rendered_html", browser),
    ):
        out = await r._makerworld_fetch_page(
            "https://makerworld.com/en/models/1-x", None
        )
    assert out == good
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_makerworld_fetch_falls_back_to_browser_on_challenge() -> None:
    challenge = "<title>Just a moment...</title><div class='cf-chl'></div>"
    rendered = '<script id="__NEXT_DATA__">{"ok":1}</script>'
    browser = AsyncMock(return_value=rendered)
    with (
        patch.object(
            r, "get_http_client", return_value=_fake_http_client(200, challenge)
        ),
        patch.object(r.browser_fetch, "fetch_rendered_html", browser),
    ):
        out = await r._makerworld_fetch_page(
            "https://makerworld.com/en/models/1-x", None
        )
    assert out == rendered
    browser.assert_awaited_once()


@pytest.mark.asyncio
async def test_makerworld_fetch_falls_back_to_browser_on_non_200() -> None:
    rendered = '<script id="__NEXT_DATA__">{}</script>'
    browser = AsyncMock(return_value=rendered)
    with (
        patch.object(r, "get_http_client", return_value=_fake_http_client(403, "")),
        patch.object(r.browser_fetch, "fetch_rendered_html", browser),
    ):
        out = await r._makerworld_fetch_page(
            "https://makerworld.com/en/models/1-x", None
        )
    assert out == rendered
    browser.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Collection classification + id extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://www.printables.com/@JonasHansen_1131321/collections/3525050",
            "printables",
        ),
        ("https://printables.com/collections/3525050", "printables"),
        (
            "https://makerworld.com/es/collections/5600774-h2d-sample-projects",
            "makerworld",
        ),
        ("https://makerworld.com/en/collections/5600774", "makerworld"),
        # A model page is not a collection.
        ("https://www.printables.com/model/1660232-springy-cat", None),
        ("https://example.com/collections/5", None),
        # Look-alike host must not classify as MakerWorld.
        ("https://evilmakerworld.com/collections/5600774", None),
    ],
)
def test_classify_collection(url: str, expected) -> None:
    assert r.classify_collection(url) == expected


def test_collection_id_extractor() -> None:
    assert (
        r._collection_id("https://printables.com/@u/collections/3525050") == "3525050"
    )
    assert (
        r._collection_id("https://makerworld.com/es/collections/5600774-slug")
        == "5600774"
    )
    assert r._collection_id("https://printables.com/model/1660232") is None


# --------------------------------------------------------------------------- #
# Printables per-file listing + selective download (real-shaped payloads)
# --------------------------------------------------------------------------- #
# Trimmed real `print(id: 1660232)` response (Springy Cat) — 11 stls across buckets.
_SPRINGY_CAT_META = {
    "data": {
        "print": {
            "id": "1660232",
            "name": "Springy Cat",
            "stls": [
                {"id": "7098445", "name": "SpringyCat.stl", "fileSize": 1233984},
                {
                    "id": "6978173",
                    "name": "SpringyCat_Spring-joiner.stl",
                    "fileSize": 1684,
                },
            ],
            "gcodes": [],
            "slas": [],
            "otherFiles": [{"id": "9001", "name": "readme.pdf", "fileSize": 4242}],
        }
    }
}


@pytest.mark.asyncio
async def test_list_model_files_lists_printables_files() -> None:
    with patch.object(
        r, "_printables_graphql", AsyncMock(return_value=_SPRINGY_CAT_META)
    ):
        result = await r.list_model_files(
            "https://www.printables.com/model/1660232-springy-cat"
        )
    assert result is not None
    title, files = result
    assert title == "Springy Cat"
    assert [(f.file_id, f.file_type, f.name) for f in files] == [
        ("7098445", "stl", "SpringyCat.stl"),
        ("6978173", "stl", "SpringyCat_Spring-joiner.stl"),
        ("9001", "other", "readme.pdf"),
    ]
    assert files[0].size == 1233984


@pytest.mark.asyncio
async def test_list_model_files_reraises_import_error_unwrapped() -> None:
    with patch.object(
        r,
        "_list_printables_files",
        AsyncMock(side_effect=ImportError_("printables_blocked")),
    ):
        with pytest.raises(ImportError_) as exc:
            await r.list_model_files("https://www.printables.com/model/1660232-x")
    assert str(exc.value) == "printables_blocked"


@pytest.mark.asyncio
async def test_list_model_files_wraps_unexpected_errors() -> None:
    with patch.object(
        r, "_list_printables_files", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(ImportError_) as exc:
            await r.list_model_files("https://www.printables.com/model/1660232-x")
    assert str(exc.value) == "printables_resolve_failed"


@pytest.mark.asyncio
async def test_list_model_files_returns_none_for_non_printables() -> None:
    # Per-file selection is Printables-only; other hosts fall back to resolve.
    assert await r.list_model_files("https://makerworld.com/en/models/1") is None
    assert await r.list_model_files("https://example.com/x.zip") is None


@pytest.mark.asyncio
async def test_resolve_selected_download_returns_per_file_links() -> None:
    chosen = [
        r.ModelFile(file_id="7098445", name="SpringyCat.stl", file_type="stl"),
        r.ModelFile(file_id="6978173", name="joiner.stl", file_type="stl"),
    ]
    payload = {
        "data": {
            "getDownloadLink": {
                "ok": True,
                "output": {
                    "link": "https://files.printables.test/joiner.stl",
                    "files": [
                        {"link": "https://files.printables.test/springycat.stl"},
                        {"link": "https://files.printables.test/joiner.stl"},
                    ],
                },
            }
        }
    }
    graphql = AsyncMock(return_value=payload)
    with patch.object(r, "_printables_graphql", graphql):
        links = await r.resolve_selected_download(
            "https://www.printables.com/model/1660232-springy-cat", chosen
        )
    assert links == [
        "https://files.printables.test/springycat.stl",
        "https://files.printables.test/joiner.stl",
    ]
    # The mutation must request exactly the chosen ids, grouped by file type.
    files_arg = graphql.call_args.args[1]["files"]
    assert files_arg == [{"fileType": "stl", "ids": ["7098445", "6978173"]}]


@pytest.mark.asyncio
async def test_resolve_selected_download_unsupported_host_raises() -> None:
    with pytest.raises(ImportError_) as exc:
        await r.resolve_selected_download("https://makerworld.com/en/models/1", [])
    assert str(exc.value) == "file_selection_unsupported"


@pytest.mark.asyncio
async def test_resolve_selected_download_reraises_import_error_unwrapped() -> None:
    with patch.object(
        r,
        "_printables_download_links",
        AsyncMock(side_effect=ImportError_("printables_blocked")),
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_selected_download(
                "https://www.printables.com/model/1660232-x", []
            )
    assert str(exc.value) == "printables_blocked"


@pytest.mark.asyncio
async def test_resolve_selected_download_wraps_unexpected_errors() -> None:
    with patch.object(
        r, "_printables_download_links", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_selected_download(
                "https://www.printables.com/model/1660232-x", []
            )
    assert str(exc.value) == "printables_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_selected_download_empty_links_raises() -> None:
    with patch.object(r, "_printables_download_links", AsyncMock(return_value=[])):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_selected_download(
                "https://www.printables.com/model/1660232-x", []
            )
    assert str(exc.value) == "printables_resolve_failed"


# --------------------------------------------------------------------------- #
# Collection resolution
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resolve_printables_collection_lists_members() -> None:
    name_payload = {"data": {"collection": {"id": "3525050", "name": "cool"}}}
    members_payload = {
        "data": {
            "moreCollectionModels": {
                "cursor": "",
                "items": [
                    {
                        "id": "1660232",
                        "print": {"id": "1660232", "name": "Springy Cat"},
                    },
                    {
                        "id": "1725199",
                        "print": {"id": "1725199", "name": "Pallet Coaster"},
                    },
                ],
            }
        }
    }
    graphql = AsyncMock(side_effect=[name_payload, members_payload])
    with patch.object(r, "_printables_graphql", graphql):
        result = await r.resolve_collection_url(
            "https://www.printables.com/@JonasHansen_1131321/collections/3525050"
        )
    assert result is not None
    title, members = result
    assert title == "cool"
    assert [(m.source_id, m.title, m.page_url) for m in members] == [
        ("1660232", "Springy Cat", "https://www.printables.com/model/1660232"),
        ("1725199", "Pallet Coaster", "https://www.printables.com/model/1725199"),
    ]


@pytest.mark.asyncio
async def test_resolve_printables_collection_paginates() -> None:
    name_payload = {"data": {"collection": {"name": "big"}}}
    page1 = {
        "data": {
            "moreCollectionModels": {
                "cursor": "next",
                "items": [{"id": "1", "print": {"id": "1", "name": "A"}}],
            }
        }
    }
    page2 = {
        "data": {
            "moreCollectionModels": {
                "cursor": "",
                "items": [{"id": "2", "print": {"id": "2", "name": "B"}}],
            }
        }
    }
    graphql = AsyncMock(side_effect=[name_payload, page1, page2])
    with patch.object(r, "_printables_graphql", graphql):
        _, members = await r.resolve_collection_url(
            "https://printables.com/collections/9"
        )
    assert [m.source_id for m in members] == ["1", "2"]


@pytest.mark.asyncio
async def test_resolve_collection_empty_raises_host_error() -> None:
    name_payload = {"data": {"collection": {"name": "empty"}}}
    members_payload = {"data": {"moreCollectionModels": {"cursor": "", "items": []}}}
    with patch.object(
        r, "_printables_graphql", AsyncMock(side_effect=[name_payload, members_payload])
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_collection_url("https://printables.com/collections/9")
    assert str(exc.value) == "printables_collection_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_collection_unknown_url_returns_none() -> None:
    assert await r.resolve_collection_url("https://example.com/collections/9") is None


@pytest.mark.asyncio
async def test_resolve_collection_reraises_import_error_unwrapped() -> None:
    with patch.object(
        r,
        "_resolve_printables_collection",
        AsyncMock(side_effect=ImportError_("printables_blocked")),
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_collection_url("https://printables.com/collections/9")
    assert str(exc.value) == "printables_blocked"


@pytest.mark.asyncio
async def test_resolve_collection_wraps_unexpected_errors() -> None:
    with patch.object(
        r, "_resolve_printables_collection", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(ImportError_) as exc:
            await r.resolve_collection_url("https://printables.com/collections/9")
    assert str(exc.value) == "printables_collection_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_makerworld_collection_walks_next_data() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "collection": {"name": "H2D Sample Projects"},
                "designs": [
                    {"id": 5600001, "title": "Sample A"},
                    {"design": {"id": 5600002, "designTitle": "Sample B"}},
                ],
            }
        }
    }
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + __import__("json").dumps(next_data)
        + "</script>"
    )
    with patch.object(r, "_makerworld_fetch_page", AsyncMock(return_value=html)):
        result = await r.resolve_collection_url(
            "https://makerworld.com/es/collections/5600774-h2d-sample-projects"
        )
    assert result is not None
    title, members = result
    assert title == "H2D Sample Projects"
    assert [(m.source_id, m.title) for m in members] == [
        ("5600001", "Sample A"),
        ("5600002", "Sample B"),
    ]
    assert members[0].page_url == "https://makerworld.com/en/models/5600001"
