"""Defends facade preserves pure rule api and adds connected providers at the services import resolvers unit boundary.

A regression would return duplicate, malformed, or provider-incomplete resolved assets.
"""

from __future__ import annotations

from ._import_resolvers_shared import (
    AsyncMock,
    ImportError_,
    ProviderTransportError,
    core_resolvers,
    httpx,
    logging,
    patch,
    pytest,
    r,
)


def test_facade_preserves_pure_rule_api_and_adds_connected_providers() -> None:
    assert r.ModelFile is core_resolvers.ModelFile
    assert r.CollectionMember is core_resolvers.CollectionMember
    printables_url = "https://www.printables.com/model/42-widget"
    assert r.classify_page(printables_url) == core_resolvers.classify_page(
        printables_url
    )
    assert (
        r.classify_page("https://www.myminifactory.com/object/123") == "myminifactory"
    )
    assert r.classify_page("https://cults3d.com/en/3d-model/art/widget") == "cults"
    assert r.classify_collection is core_resolvers.classify_collection
    assert r._printables_id is core_resolvers.printables_id
    assert r._makerworld_id is core_resolvers.makerworld_id
    assert r._thingiverse_id is core_resolvers.thingiverse_id
    assert r._collection_id is core_resolvers.collection_id
    assert r._looks_like_download is core_resolvers.looks_like_download
    assert r._first_download_url is core_resolvers.first_download_url
    assert r._looks_like_challenge is core_resolvers.looks_like_challenge
    assert r._extract_next_data is core_resolvers.extract_next_data
    assert r._pick_printables_pack is core_resolvers.pick_printables_pack
    assert r._printables_link_from_output is core_resolvers.printables_link_from_output
    assert (
        r._printables_links_from_output is core_resolvers.printables_links_from_output
    )
    assert r._printables_files_from_print is core_resolvers.printables_files_from_print
    assert (
        r._makerworld_collection_members is core_resolvers.makerworld_collection_members
    )
    assert r.parse_printables_capture is core_resolvers.parse_printables_capture


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
    response = httpx.Response(403, content=b"upstream body")

    class FakeTransport:
        async def request(self, *args, **kwargs):
            return response

    with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
        with pytest.raises(ImportError_) as exc:
            await r._printables_graphql("query", {}, "https://printables.com")
    assert str(exc.value) == "printables_blocked"


@pytest.mark.asyncio
async def test_printables_graphql_returns_json_on_success() -> None:
    response = httpx.Response(
        200,
        json={"data": {}},
        request=httpx.Request("POST", r._PRINTABLES_GRAPHQL),
    )
    calls = []

    class FakeTransport:
        async def request(self, *args, **kwargs):
            calls.append((args, kwargs))
            return response

    with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
        out = await r._printables_graphql("query", {}, "https://printables.com")
    assert out == {"data": {}}
    assert calls == [
        (
            ("POST", r._PRINTABLES_GRAPHQL),
            {
                "json": {"query": "query", "variables": {}},
                "headers": {
                    "User-Agent": r._BROWSER_UA,
                    "Accept": "application/json",
                    "Origin": "https://www.printables.com",
                    "Referer": "https://printables.com",
                },
                "allowed_hosts": frozenset({"api.printables.com"}),
            },
        )
    ]


@pytest.mark.asyncio
async def test_printables_graphql_maps_transport_failures_to_stable_redacted_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeTransport:
        async def request(self, *args, **kwargs):
            raise ProviderTransportError("provider_retry_exhausted", retryable=True)

    with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
        with caplog.at_level(logging.WARNING, logger="app.services.import_resolvers"):
            with pytest.raises(ImportError_) as exc:
                await r._printables_graphql(
                    "query",
                    {"token": "upstream-secret"},
                    "https://www.printables.com/model/1?token=query-secret",
                )

    assert str(exc.value) == "printables_resolve_failed"
    assert "upstream-secret" not in caplog.text
    assert "query-secret" not in caplog.text
    assert "provider_retry_exhausted" in caplog.text


@pytest.mark.asyncio
async def test_printables_graphql_preserves_rate_limit_blocked_semantics() -> None:
    class FakeTransport:
        async def request(self, *args, **kwargs):
            raise ProviderTransportError(
                "provider_retry_exhausted", retryable=True, status_code=429
            )

    with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
        with pytest.raises(ImportError_, match="printables_blocked"):
            await r._printables_graphql("query", {}, "https://printables.com")


@pytest.mark.asyncio
async def test_resolve_capture_manifest_returns_strict_printables_v2() -> None:
    payload = {
        "data": {
            "print": {
                "id": "3161",
                "name": "3D Benchy",
                "stls": [{"id": "stl-1", "name": "benchy.stl", "fileSize": 12}],
                "gcodes": [],
                "slas": [],
                "otherFiles": [],
            }
        }
    }
    graphql = AsyncMock(return_value=payload)
    with patch.object(r, "_printables_graphql", graphql):
        manifest = await r.resolve_capture_manifest(
            "https://www.printables.com/model/3161-3d-benchy?foo=bar"
        )

    assert manifest is not None
    assert manifest.schema_version == 2
    assert (
        manifest.source.canonical_url
        == "https://www.printables.com/model/3161-3d-benchy"
    )
    assert manifest.selected_ids == ("stl-1",)


@pytest.mark.asyncio
async def test_resolve_capture_manifest_ignores_non_printables() -> None:
    assert await r.resolve_capture_manifest("https://example.com/model.stl") is None


@pytest.mark.asyncio
async def test_resolve_selected_assets_reorders_shuffled_provider_response() -> None:
    manifest = core_resolvers.parse_printables_capture(
        {
            "data": {
                "print": {
                    "id": "3161",
                    "name": "3D Benchy",
                    "stls": [
                        {"id": "first", "name": "first.stl", "fileSize": 1},
                        {"id": "second", "name": "second.stl", "fileSize": 1},
                    ],
                    "gcodes": [],
                    "slas": [],
                    "otherFiles": [],
                }
            }
        },
        "https://www.printables.com/model/3161-3d-benchy",
    )
    payload = {
        "data": {
            "getDownloadLink": {
                "output": {
                    "files": [
                        {"id": "second", "link": "https://files.test/second.stl"},
                        {"id": "first", "link": "https://files.test/first.stl"},
                    ]
                }
            }
        }
    }
    with patch.object(r, "_printables_graphql", AsyncMock(return_value=payload)):
        assets = await r.resolve_selected_assets(
            "https://www.printables.com/model/3161-3d-benchy",
            manifest,
            ["first", "second"],
        )

    assert [(asset.source_selection_id, asset.download_url) for asset in assets] == [
        ("first", "https://files.test/first.stl"),
        ("second", "https://files.test/second.stl"),
    ]


@pytest.mark.asyncio
async def test_resolve_unknown_host_returns_none() -> None:
    # No network: unknown hosts short-circuit before any resolver runs.
    assert await r.resolve_page_url("https://example.com/foo.zip") is None


@pytest.mark.asyncio
async def test_resolve_thingiverse_requires_browser_assisted_manual_capture() -> None:
    url = "https://www.thingiverse.com/thing:763622/files"
    with pytest.raises(ImportError_) as exc:
        await r.resolve_page_url(url)

    assert str(exc.value) == "thingiverse_extension_required"


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
async def test_provider_resolution_logs_redact_url_queries_and_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(
        r,
        "_resolve_printables",
        AsyncMock(side_effect=RuntimeError("signed token=upstream-secret")),
    ):
        with caplog.at_level(logging.WARNING, logger="app.services.import_resolvers"):
            with pytest.raises(ImportError_, match="printables_resolve_failed"):
                await r.resolve_page_url(
                    "https://www.printables.com/model/3161-benchy?token=query-secret"
                )

    assert "query-secret" not in caplog.text
    assert "upstream-secret" not in caplog.text
    assert "RuntimeError" in caplog.text


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
async def test_resolve_makerworld_requires_browser_extension() -> None:
    with pytest.raises(ImportError_) as exc:
        await r.resolve_page_url(
            "https://makerworld.com/en/models/1123776-x",
            makerworld_cookie="legacy-cookie-must-not-be-used",
        )
    assert str(exc.value) == "makerworld_extension_required"


def test_makerworld_collection_members_handles_malformed_next_data() -> None:
    assert r._makerworld_collection_members({}) == []
    assert r._makerworld_collection_members({"props": None}) == []
