"""Defends makerworld collection members skips non dict and duplicate entries at the services import resolvers unit boundary.

A regression would return duplicate, malformed, or provider-incomplete resolved assets.
"""

from __future__ import annotations

from ._import_resolvers_shared import (
    _SPRINGY_CAT_META,
    AsyncMock,
    ImportError_,
    patch,
    pytest,
    r,
)


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
async def test_resolve_makerworld_collection_requires_browser_extension() -> None:
    with pytest.raises(ImportError_) as exc:
        await r.resolve_collection_url(
            "https://makerworld.com/en/collections/5-x",
            makerworld_cookie="legacy-cookie-must-not-be-used",
        )
    assert str(exc.value) == "makerworld_extension_required"


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
