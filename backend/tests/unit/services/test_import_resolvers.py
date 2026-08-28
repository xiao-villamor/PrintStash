"""Unit coverage for ``import_resolvers`` — turning model *page* URLs into
direct download URLs.

The host HTTP calls (Printables GraphQL, MakerWorld page + API) are patched at
the module's small network helpers, so these tests exercise the dispatch, id
extraction, pack selection and JSON-walking logic without any real network.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from printstash_core.imports import resolvers as core_resolvers

from app.services import import_resolvers as r
from app.services.capture_provider_transport import ProviderTransportError
from app.services.importer import ImportError_


class TestFacadeSurface:
    """The app module still re-exports the pure rules `printstash-core` owns.

    Every rule helper moved to core, and the app module keeps aliases so the
    hundreds of existing call sites and tests did not have to change. An alias
    that quietly stops pointing at core is the failure this guards: the name
    still resolves, the code still runs, and it runs a stale copy of the rule."""

    def test_the_facade_re_exports_the_pure_rule_api_intact(self) -> None:
        assert r.ModelFile is core_resolvers.ModelFile
        assert r.CollectionMember is core_resolvers.CollectionMember
        printables_url = "https://www.printables.com/model/42-widget"
        assert r.classify_page(printables_url) == core_resolvers.classify_page(
            printables_url
        )
        assert (
            r.classify_page("https://www.myminifactory.com/object/123")
            == "myminifactory"
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
        assert (
            r._printables_link_from_output is core_resolvers.printables_link_from_output
        )
        assert (
            r._printables_links_from_output
            is core_resolvers.printables_links_from_output
        )
        assert (
            r._printables_files_from_print is core_resolvers.printables_files_from_print
        )
        assert (
            r._makerworld_collection_members
            is core_resolvers.makerworld_collection_members
        )
        assert r.parse_printables_capture is core_resolvers.parse_printables_capture


# --------------------------------------------------------------------------- #
# Host classification + id extraction (pure functions)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Generic JSON helpers
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolve_page_url dispatch
# --------------------------------------------------------------------------- #
class TestResolvePageUrl:
    @pytest.mark.asyncio
    async def test_resolve_unknown_host_returns_none(self) -> None:
        # No network: unknown hosts short-circuit before any resolver runs.
        assert await r.resolve_page_url("https://example.com/foo.zip") is None

    @pytest.mark.asyncio
    async def test_resolve_thingiverse_requires_browser_assisted_manual_capture(
        self,
    ) -> None:
        url = "https://www.thingiverse.com/thing:763622/files"
        with pytest.raises(ImportError_) as exc:
            await r.resolve_page_url(url)

        assert str(exc.value) == "thingiverse_extension_required"

    @pytest.mark.asyncio
    async def test_resolve_makerworld_requires_browser_extension(self) -> None:
        with pytest.raises(ImportError_) as exc:
            await r.resolve_page_url(
                "https://makerworld.com/en/models/1123776-x",
                makerworld_cookie="legacy-cookie-must-not-be-used",
            )
        assert str(exc.value) == "makerworld_extension_required"

    @pytest.mark.asyncio
    async def test_provider_resolution_logs_redact_every_secret_they_touch(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with patch.object(
            r,
            "_resolve_printables",
            AsyncMock(side_effect=RuntimeError("signed token=upstream-secret")),
        ):
            with caplog.at_level(
                logging.WARNING, logger="app.services.import_resolvers"
            ):
                with pytest.raises(ImportError_, match="printables_resolve_failed"):
                    await r.resolve_page_url(
                        "https://www.printables.com/model/3161-benchy?token=query-secret"
                    )

        assert "query-secret" not in caplog.text
        assert "upstream-secret" not in caplog.text
        assert "RuntimeError" in caplog.text


class TestResolveCollectionUrl:
    @pytest.mark.asyncio
    async def test_resolve_collection_unknown_url_returns_none(self) -> None:
        assert (
            await r.resolve_collection_url("https://example.com/collections/9") is None
        )

    @pytest.mark.asyncio
    async def test_resolve_makerworld_collection_requires_browser_extension(
        self,
    ) -> None:
        with pytest.raises(ImportError_) as exc:
            await r.resolve_collection_url(
                "https://makerworld.com/en/collections/5-x",
                makerworld_cookie="legacy-cookie-must-not-be-used",
            )
        assert str(exc.value) == "makerworld_extension_required"

    @pytest.mark.asyncio
    async def test_resolve_collection_reraises_import_error_unwrapped(self) -> None:
        with patch.object(
            r,
            "_resolve_printables_collection",
            AsyncMock(side_effect=ImportError_("printables_blocked")),
        ):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_collection_url("https://printables.com/collections/9")
        assert str(exc.value) == "printables_blocked"

    @pytest.mark.asyncio
    async def test_resolve_collection_wraps_unexpected_errors(self) -> None:
        with patch.object(
            r,
            "_resolve_printables_collection",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_collection_url("https://printables.com/collections/9")
        assert str(exc.value) == "printables_collection_resolve_failed"

    @pytest.mark.asyncio
    async def test_resolve_collection_empty_raises_host_error(self) -> None:
        name_payload = {"data": {"collection": {"name": "empty"}}}
        members_payload = {
            "data": {"moreCollectionModels": {"cursor": "", "items": []}}
        }
        with patch.object(
            r,
            "_printables_graphql",
            AsyncMock(side_effect=[name_payload, members_payload]),
        ):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_collection_url("https://printables.com/collections/9")
        assert str(exc.value) == "printables_collection_resolve_failed"


# --------------------------------------------------------------------------- #
# Provider payload characterization
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Collection classification + id extraction
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Collection resolution
# --------------------------------------------------------------------------- #


class TestPrintablesGraphql:
    @pytest.mark.asyncio
    async def test_printables_graphql_raises_on_blocked_status(self) -> None:
        response = httpx.Response(403, content=b"upstream body")

        class FakeTransport:
            async def request(self, *args, **kwargs):
                return response

        with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
            with pytest.raises(ImportError_) as exc:
                await r._printables_graphql("query", {}, "https://printables.com")
        assert str(exc.value) == "printables_blocked"

    @pytest.mark.asyncio
    async def test_printables_graphql_returns_json_on_success(self) -> None:
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
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        class FakeTransport:
            async def request(self, *args, **kwargs):
                raise ProviderTransportError("provider_retry_exhausted", retryable=True)

        with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
            with caplog.at_level(
                logging.WARNING, logger="app.services.import_resolvers"
            ):
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
    async def test_printables_graphql_preserves_rate_limit_blocked_semantics(
        self,
    ) -> None:
        class FakeTransport:
            async def request(self, *args, **kwargs):
                raise ProviderTransportError(
                    "provider_retry_exhausted", retryable=True, status_code=429
                )

        with patch.object(r, "ProviderTransport", return_value=FakeTransport()):
            with pytest.raises(ImportError_, match="printables_blocked"):
                await r._printables_graphql("query", {}, "https://printables.com")


class TestResolvePrintables:
    @pytest.mark.asyncio
    async def test_resolve_printables_uses_pack_link(self) -> None:
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
    async def test_resolve_printables_unresolved_raises_host_error(self) -> None:
        meta = {"data": {"print": {"id": "3161", "downloadPacks": [], "stls": []}}}
        with patch.object(r, "_printables_graphql", AsyncMock(return_value=meta)):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_page_url(
                    "https://www.printables.com/model/3161-3d-benchy"
                )
        assert str(exc.value) == "printables_resolve_failed"

    @pytest.mark.asyncio
    async def test_resolve_printables_network_error_becomes_host_error(self) -> None:
        with patch.object(
            r, "_printables_graphql", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_page_url(
                    "https://www.printables.com/model/3161-3d-benchy"
                )
        assert str(exc.value) == "printables_resolve_failed"

    @pytest.mark.asyncio
    async def test_resolve_printables_no_id_returns_none(self) -> None:
        assert (
            await r._resolve_printables("https://www.printables.com/social/1-x") is None
        )

    @pytest.mark.asyncio
    async def test_resolve_printables_no_print_object_returns_none(self) -> None:
        with patch.object(
            r, "_printables_graphql", AsyncMock(return_value={"data": {}})
        ):
            assert (
                await r._resolve_printables("https://www.printables.com/model/3161-x")
                is None
            )

    @pytest.mark.asyncio
    async def test_resolve_printables_falls_back_to_stl_ids_when_pack_link_missing(
        self,
    ) -> None:
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


class TestResolveCaptureManifest:
    @pytest.mark.asyncio
    async def test_resolve_capture_manifest_returns_strict_printables_v2(self) -> None:
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
    async def test_resolve_capture_manifest_ignores_non_printables(self) -> None:
        assert await r.resolve_capture_manifest("https://example.com/model.stl") is None


class TestListPrintablesFiles:
    @pytest.mark.asyncio
    async def test_list_printables_files_no_print_id_returns_none(self) -> None:
        assert (
            await r._list_printables_files("https://www.printables.com/social/1-x")
            is None
        )

    @pytest.mark.asyncio
    async def test_list_printables_files_no_print_object_returns_none(self) -> None:
        with patch.object(
            r, "_printables_graphql", AsyncMock(return_value={"data": {}})
        ):
            assert (
                await r._list_printables_files(
                    "https://www.printables.com/model/3161-x"
                )
                is None
            )


class TestPrintablesDownloadLinks:
    @pytest.mark.asyncio
    async def test_printables_download_links_no_files_returns_empty(self) -> None:
        assert (
            await r._printables_download_links(
                "https://www.printables.com/model/3161-x", []
            )
            == []
        )


class TestResolvePrintablesCollection:
    @pytest.mark.asyncio
    async def test_resolve_printables_collection_no_id_returns_none(self) -> None:
        assert (
            await r._resolve_printables_collection("https://printables.com/social/1-x")
            is None
        )

    @pytest.mark.asyncio
    async def test_printables_collection_members_are_deduplicated(
        self,
    ) -> None:
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
    async def test_resolve_printables_collection_lists_members(self) -> None:
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
    async def test_resolve_printables_collection_paginates(self) -> None:
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


class TestListModelFiles:
    @pytest.mark.asyncio
    async def test_list_model_files_lists_printables_files(self) -> None:
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
    async def test_list_model_files_reraises_import_error_unwrapped(self) -> None:
        with patch.object(
            r,
            "_list_printables_files",
            AsyncMock(side_effect=ImportError_("printables_blocked")),
        ):
            with pytest.raises(ImportError_) as exc:
                await r.list_model_files("https://www.printables.com/model/1660232-x")
        assert str(exc.value) == "printables_blocked"

    @pytest.mark.asyncio
    async def test_list_model_files_wraps_unexpected_errors(self) -> None:
        with patch.object(
            r, "_list_printables_files", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(ImportError_) as exc:
                await r.list_model_files("https://www.printables.com/model/1660232-x")
        assert str(exc.value) == "printables_resolve_failed"

    @pytest.mark.asyncio
    async def test_list_model_files_returns_none_for_non_printables(self) -> None:
        # Per-file selection is Printables-only; other hosts fall back to resolve.
        assert await r.list_model_files("https://makerworld.com/en/models/1") is None
        assert await r.list_model_files("https://example.com/x.zip") is None


class TestResolveSelectedDownload:
    @pytest.mark.asyncio
    async def test_resolve_selected_download_returns_per_file_links(self) -> None:
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
    async def test_resolve_selected_download_unsupported_host_raises(self) -> None:
        with pytest.raises(ImportError_) as exc:
            await r.resolve_selected_download("https://makerworld.com/en/models/1", [])
        assert str(exc.value) == "file_selection_unsupported"

    @pytest.mark.asyncio
    async def test_resolve_selected_download_reraises_import_error_unwrapped(
        self,
    ) -> None:
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
    async def test_resolve_selected_download_wraps_unexpected_errors(self) -> None:
        with patch.object(
            r, "_printables_download_links", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_selected_download(
                    "https://www.printables.com/model/1660232-x", []
                )
        assert str(exc.value) == "printables_resolve_failed"

    @pytest.mark.asyncio
    async def test_resolve_selected_download_empty_links_raises(self) -> None:
        with patch.object(r, "_printables_download_links", AsyncMock(return_value=[])):
            with pytest.raises(ImportError_) as exc:
                await r.resolve_selected_download(
                    "https://www.printables.com/model/1660232-x", []
                )
        assert str(exc.value) == "printables_resolve_failed"


class TestResolveSelectedAssets:
    @pytest.mark.asyncio
    async def test_resolve_selected_assets_reorders_shuffled_provider_response(
        self,
    ) -> None:
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

        assert [
            (asset.source_selection_id, asset.download_url) for asset in assets
        ] == [
            ("first", "https://files.test/first.stl"),
            ("second", "https://files.test/second.stl"),
        ]
