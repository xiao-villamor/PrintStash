"""URL classification and provider-payload normalization for imports.

Two different kinds of rule live in this module, and both are security-relevant
in a way that a "parsing helper" usually is not.

**Host classification decides whether a URL is trusted.** `classify_page` is what
tells the importer "this is a Printables model page", and everything downstream —
which adapter runs, which cookies are attached, which download is followed —
follows from that answer. A suffix match instead of an exact-or-subdomain match
would classify `makerworld.com.attacker.test` as MakerWorld and hand a hostile
host the importer's session. Those near-miss hostnames are rows here.

**Payload normalization decides what gets persisted.** A provider's JSON response
carries far more than PrintStash wants: browser state, session hints, signed
download links with embedded credentials. `parse_printables_capture` exists to
take only a small reviewed allowlist and push it through the strict manifest
boundary, so the test that matters most is not "does it read the title" but
"does everything *outside* the allowlist get dropped".

The rest is tolerance. Provider hydration JSON changes shape without notice —
`user` becomes `creator`, `license` is a string one week and an object the next,
a design is nested under `design` or is the entry itself — so every extractor
takes the shapes seen in the wild and returns `None` rather than raising on one
it has not. An importer that throws on a shape change fails the whole import;
one that returns `None` imports the model with a missing field.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from printstash_core.imports import classify_page, resolvers
from printstash_core.imports.contracts import CaptureContractError

CANONICAL_URL = "https://www.printables.com/model/3161-3d-benchy"

# A Printables model response reduced to the fields the adapter reads, plus two
# it must not: a session cookie and a signed download link.
PRINTABLES_PAYLOAD: dict[str, Any] = {
    "data": {
        "print": {
            "id": 3161,
            "name": "3DBenchy",
            "description": "The jolly torture test.",
            "instructions": "Print at 0.2mm.",
            "user": {
                "handle": "creativetools",
                "id": 42,
                "url": "https://www.printables.com/@creativetools",
            },
            "license": {
                "code": "CC-BY-4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
                "text": "Attribution 4.0 International",
            },
            "attribution": "Model by Creative Tools",
            "stls": [{"id": 7, "name": "benchy.stl", "fileSize": 123}],
            "sessionToken": "not-a-real-session-token",
            "downloadLink": "https://cdn.test/signed?token=not-a-real-token",
        }
    }
}


def capture(**print_fields: Any) -> Any:
    """Parse a minimal Printables print object with the given fields.

    A capture manifest must name at least one file — a model page with nothing
    downloadable is not importable — so every minimal payload carries one. The
    id has to match the one in `CANONICAL_URL`, because the manifest boundary
    cross-checks them: a capture whose URL names a different model than its
    payload is a mix-up, not a valid import.
    """

    return resolvers.parse_printables_capture(
        {
            "data": {
                "print": {
                    "id": 3161,
                    "stls": [{"id": 7, "name": "part.stl"}],
                    **print_fields,
                }
            }
        },
        CANONICAL_URL,
    )


def next_data_html(payload: object) -> str:
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script>"
    )


class TestPackageExports:
    def test_exports_the_classifier_from_the_package_root(self) -> None:
        # Callers import from `printstash_core.imports`, so re-export drift
        # would silently give them a different function.
        assert classify_page is resolvers.classify_page


class TestHost:
    def test_lower_cases_the_hostname(self) -> None:
        assert resolvers.host("https://WWW.Printables.COM/model/1") == (
            "www.printables.com"
        )

    def test_returns_nothing_for_a_string_that_is_not_a_url(self) -> None:
        assert resolvers.host("not a url") == ""


class TestPrintablesId:
    def test_reads_the_id_from_a_model_path(self) -> None:
        assert resolvers.printables_id("https://printables.com/model/3161-x") == "3161"

    def test_returns_nothing_for_a_path_with_no_model_segment(self) -> None:
        assert resolvers.printables_id("https://printables.com/@user") is None


class TestMakerworldId:
    def test_reads_the_id_from_a_models_path(self) -> None:
        assert resolvers.makerworld_id("https://makerworld.com/en/models/12-x") == "12"

    def test_returns_nothing_for_a_path_with_no_models_segment(self) -> None:
        assert resolvers.makerworld_id("https://makerworld.com/en/search") is None


class TestThingiverseId:
    def test_reads_the_id_from_the_colon_form(self) -> None:
        assert (
            resolvers.thingiverse_id("https://thingiverse.com/thing:763622") == "763622"
        )

    def test_reads_the_id_from_the_plural_path_form(self) -> None:
        # Thingiverse serves both; a link pasted from search uses `/things/`.
        assert resolvers.thingiverse_id("https://thingiverse.com/things/8/files") == "8"

    def test_returns_nothing_for_a_path_with_neither_form(self) -> None:
        assert resolvers.thingiverse_id("https://thingiverse.com/search") is None


class TestCollectionId:
    def test_reads_the_id_from_a_collections_path(self) -> None:
        assert resolvers.collection_id("https://printables.com/collections/9-x") == "9"

    def test_returns_nothing_for_a_model_path(self) -> None:
        assert resolvers.collection_id("https://printables.com/model/9-x") is None


class TestClassifyPage:
    @pytest.mark.parametrize(
        ("url", "provider"),
        [
            ("https://www.printables.com/model/3161-3d-benchy", "printables"),
            ("https://printables.com/model/3161", "printables"),
            ("https://makerworld.com/en/models/1123776-benchy", "makerworld"),
            ("https://assets.makerworld.com/en/models/1123776-benchy", "makerworld"),
            ("https://www.thingiverse.com/thing:763622/files", "thingiverse"),
            ("https://www.thingiverse.com/things/763622/files", "thingiverse"),
        ],
    )
    def test_recognizes_a_model_page_on_a_supported_host(
        self, url: str, provider: str
    ) -> None:
        assert resolvers.classify_page(url) == provider

    @pytest.mark.parametrize(
        "url",
        [
            "https://evilmakerworld.com/en/models/1123776",
            "https://makerworld.com.attacker.test/models/1123776",
            "https://printables.com.attacker.test/model/3161",
            "https://notprintables.com/model/3161",
        ],
    )
    def test_refuses_a_hostname_that_only_resembles_a_supported_one(
        self, url: str
    ) -> None:
        # This answer decides which adapter runs and which session is attached,
        # so a suffix match here would hand a hostile host the importer's
        # credentials.
        assert resolvers.classify_page(url) is None

    def test_refuses_a_supported_host_with_no_model_id_in_the_path(self) -> None:
        assert (
            resolvers.classify_page("https://files.printables.com/x/file.stl") is None
        )

    def test_refuses_a_string_that_is_not_a_url(self) -> None:
        assert resolvers.classify_page("not a url") is None


class TestClassifyCollection:
    @pytest.mark.parametrize(
        ("url", "provider"),
        [
            ("https://printables.com/@u/collections/3525050", "printables"),
            ("https://makerworld.com/en/collections/5600774-slug", "makerworld"),
            ("https://x.makerworld.com/collections/5600774", "makerworld"),
        ],
    )
    def test_recognizes_a_collection_on_a_supported_host(
        self, url: str, provider: str
    ) -> None:
        assert resolvers.classify_collection(url) == provider

    def test_refuses_a_hostname_that_only_resembles_a_supported_one(self) -> None:
        assert (
            resolvers.classify_collection("https://evilmakerworld.com/collections/1")
            is None
        )

    def test_refuses_a_model_page(self) -> None:
        # A model URL and a collection URL start different import flows.
        assert resolvers.classify_collection("https://printables.com/model/1") is None

    def test_refuses_thingiverse_which_has_no_collection_support(self) -> None:
        assert (
            resolvers.classify_collection("https://thingiverse.com/collections/1")
            is None
        )


class TestLooksLikeDownload:
    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn.test/model.stl",
            "https://cdn.test/model.3MF?download=1",
            "https://cdn.test/pack.zip",
            "https://cdn.test/part.bgcode",
            "https://cdn.test/api/download/123",
        ],
    )
    def test_recognizes_a_model_file_or_a_download_route(self, url: str) -> None:
        assert resolvers.looks_like_download(url) is True

    @pytest.mark.parametrize(
        "url", ["https://cdn.test/get?file=model.stl", "https://cdn.test/image.png"]
    )
    def test_refuses_a_url_whose_path_names_no_model_file(self, url: str) -> None:
        # The extension has to be in the *path*: a query parameter naming an STL
        # is a page that renders one, not the file.
        assert resolvers.looks_like_download(url) is False


class TestFirstDownloadUrl:
    def test_prefers_a_keyed_url_over_a_bare_string(self) -> None:
        payload = {
            "files": ["https://cdn.test/fallback.stl"],
            "nested": {"downloadUrl": "https://cdn.test/preferred.zip"},
        }

        # A keyed link is the provider telling us which URL to use; a bare
        # string that happens to look like a file is a guess.
        assert resolvers.first_download_url(payload) == "https://cdn.test/preferred.zip"

    @pytest.mark.parametrize("key", ["url", "downloadUrl", "download_url", "link"])
    def test_reads_every_key_a_provider_uses_for_the_link(self, key: str) -> None:
        assert (
            resolvers.first_download_url({key: "https://cdn.test/a.zip"})
            == "https://cdn.test/a.zip"
        )

    def test_falls_back_to_a_bare_url_that_names_a_model_file(self) -> None:
        assert (
            resolvers.first_download_url(["https://cdn.test/model.stl"])
            == "https://cdn.test/model.stl"
        )

    def test_refuses_a_relative_url(self) -> None:
        # A relative link would be resolved against whatever base the caller
        # happened to hold, which is how an importer ends up fetching itself.
        assert resolvers.first_download_url({"url": "/relative.stl"}) is None

    def test_refuses_a_non_http_scheme(self) -> None:
        assert resolvers.first_download_url({"url": "file:///etc/passwd"}) is None

    def test_returns_nothing_for_a_payload_with_no_links(self) -> None:
        assert resolvers.first_download_url({"title": "Benchy"}) is None

    def test_searches_breadth_first_so_a_shallow_link_wins(self) -> None:
        payload = {
            "deep": {"deeper": {"link": "https://cdn.test/deep.zip"}},
            "shallow": {"link": "https://cdn.test/shallow.zip"},
        }

        assert resolvers.first_download_url(payload) == "https://cdn.test/shallow.zip"


class TestLooksLikeChallenge:
    @pytest.mark.parametrize(
        "marker",
        [
            "just a moment",
            "challenge-platform",
            "cf-chl",
            "verifying you are human",
            "/cdn-cgi/challenge-platform/",
        ],
    )
    def test_recognizes_every_known_challenge_marker(self, marker: str) -> None:
        # Misreading a challenge page as content persists an interstitial as the
        # model's description.
        assert resolvers.looks_like_challenge(f"<html>{marker}</html>") is True

    def test_matches_a_marker_regardless_of_case(self) -> None:
        assert resolvers.looks_like_challenge("<title>Just A Moment...</title>") is True

    def test_treats_a_page_carrying_hydration_data_as_content(self) -> None:
        # A real page can mention "just a moment" in its own copy. The presence
        # of `__NEXT_DATA__` proves the app rendered, so content wins.
        assert (
            resolvers.looks_like_challenge(
                '<script id="__NEXT_DATA__">{}</script> just a moment'
            )
            is False
        )

    def test_treats_an_ordinary_page_as_content(self) -> None:
        assert resolvers.looks_like_challenge("<html><h1>3DBenchy</h1></html>") is False


class TestExtractNextData:
    def test_reads_the_hydration_payload(self) -> None:
        payload = {"props": {"pageProps": {"ok": True}}}

        assert resolvers.extract_next_data(next_data_html(payload)) == payload

    def test_returns_nothing_when_the_page_has_no_hydration_script(self) -> None:
        assert resolvers.extract_next_data("<html><body>hi</body></html>") is None

    def test_returns_nothing_when_the_payload_is_not_valid_json(self) -> None:
        # A truncated response must import as "no metadata", not as a crash.
        assert (
            resolvers.extract_next_data("<script id='__NEXT_DATA__'>{bad}</script>")
            is None
        )


class TestPickPrintablesPack:
    def test_prefers_the_pack_holding_every_model_file(self) -> None:
        packs = [{"id": 5, "fileType": "OTHER"}, {"id": 9, "fileType": "MODEL_FILES"}]

        # Importing the "other files" pack gets the reader a folder of PDFs and
        # no geometry.
        assert resolvers.pick_printables_pack(packs) == "9"

    def test_falls_back_to_the_first_pack_carrying_an_id(self) -> None:
        assert resolvers.pick_printables_pack([{"id": 7}]) == "7"

    def test_skips_a_pack_with_no_id(self) -> None:
        assert (
            resolvers.pick_printables_pack([{"fileType": "MODEL_FILES"}, {"id": 3}])
            == "3"
        )

    def test_returns_nothing_when_the_packs_are_not_a_list(self) -> None:
        assert resolvers.pick_printables_pack(None) is None

    def test_returns_nothing_for_an_empty_pack_list(self) -> None:
        assert resolvers.pick_printables_pack([]) is None


class TestPrintablesLinkFromOutput:
    def test_prefers_the_single_pack_link(self) -> None:
        payload = {
            "data": {
                "getDownloadLink": {
                    "output": {
                        "link": "https://cdn.test/pack.zip",
                        "files": [{"link": "https://cdn.test/a.stl"}],
                    }
                }
            }
        }

        assert (
            resolvers.printables_link_from_output(payload)
            == "https://cdn.test/pack.zip"
        )

    def test_falls_back_to_the_first_per_file_link(self) -> None:
        payload = {
            "data": {
                "getDownloadLink": {
                    "output": {"files": [{"link": "https://cdn.test/a.stl"}]}
                }
            }
        }

        assert (
            resolvers.printables_link_from_output(payload) == "https://cdn.test/a.stl"
        )

    def test_returns_nothing_for_an_empty_response(self) -> None:
        assert resolvers.printables_link_from_output({}) is None

    def test_returns_nothing_for_a_missing_response(self) -> None:
        assert resolvers.printables_link_from_output(None) is None


class TestPrintablesLinksFromOutput:
    def test_returns_every_per_file_link(self) -> None:
        payload = {
            "data": {
                "getDownloadLink": {
                    "output": {
                        "link": "https://cdn.test/pack.zip",
                        "files": [
                            {"link": "https://cdn.test/a.stl"},
                            {"link": "https://cdn.test/b.stl"},
                        ],
                    }
                }
            }
        }

        # Per-file links let the importer honour the user's file selection; the
        # pack link is all-or-nothing.
        assert resolvers.printables_links_from_output(payload) == [
            "https://cdn.test/a.stl",
            "https://cdn.test/b.stl",
        ]

    def test_falls_back_to_the_single_pack_link(self) -> None:
        payload = {
            "data": {
                "getDownloadLink": {"output": {"link": "https://cdn.test/pack.zip"}}
            }
        }

        assert resolvers.printables_links_from_output(payload) == [
            "https://cdn.test/pack.zip"
        ]

    def test_skips_an_entry_with_no_link(self) -> None:
        payload = {
            "data": {
                "getDownloadLink": {
                    "output": {
                        "files": [{"name": "a.stl"}, {"link": "https://c/b.stl"}]
                    }
                }
            }
        }

        assert resolvers.printables_links_from_output(payload) == ["https://c/b.stl"]

    def test_returns_nothing_for_an_empty_response(self) -> None:
        assert resolvers.printables_links_from_output({}) == []


class TestPrintablesFilesFromPrint:
    def test_normalizes_every_file_bucket_into_typed_entries(self) -> None:
        files = resolvers.printables_files_from_print(
            {
                "stls": [{"id": 7, "name": "part.stl", "fileSize": 123}],
                "gcodes": [{"id": "8", "name": "part.gcode"}],
                "slas": [{"id": 9, "name": "resin.ctb"}],
                "otherFiles": [{"id": 10, "name": "notes.pdf"}],
            }
        )

        # The type drives what PrintStash does with the bytes — mesh preview,
        # G-code parse, or plain attachment.
        assert [file.file_type for file in files] == ["stl", "gcode", "sla", "other"]

    def test_names_a_file_by_its_id_when_the_name_is_empty(self) -> None:
        files = resolvers.printables_files_from_print(
            {"gcodes": [{"id": "8", "name": ""}]}
        )

        assert files == [resolvers.ModelFile("8", "8", "gcode", None)]

    def test_drops_a_size_that_is_not_a_number(self) -> None:
        files = resolvers.printables_files_from_print(
            {"stls": [{"id": 7, "name": "a.stl", "fileSize": "unknown"}]}
        )

        assert files[0].size is None

    def test_skips_an_entry_with_no_id(self) -> None:
        # Without an id the file cannot be requested, so listing it would offer
        # the user a selection that fails on download.
        files = resolvers.printables_files_from_print(
            {"stls": [{"id": None, "name": "ignored.stl"}]}
        )

        assert files == []

    def test_returns_nothing_for_a_print_with_no_files(self) -> None:
        assert resolvers.printables_files_from_print({}) == []


class TestParsePrintablesCapture:
    def test_captures_the_metadata_the_library_shows(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        assert manifest.source.fields["title"].value == "3DBenchy"
        assert manifest.source.fields["creator_name"].value == "creativetools"
        assert manifest.source.fields["license_code"].value == "CC-BY-4.0"

    def test_drops_everything_outside_the_reviewed_allowlist(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        # This is the whole point of the adapter. A provider response carries
        # session state and signed links; persisting either would store a
        # credential in the library database.
        persisted = json.dumps(manifest.to_dict())
        assert "not-a-real-session-token" not in persisted
        assert "not-a-real-token" not in persisted

    def test_marks_every_captured_field_as_confirmed(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        # These came from the provider's own API rather than being inferred, so
        # the UI presents them without a "check this" prompt.
        assert {field.origin for field in manifest.source.fields.values()} == {
            "confirmed"
        }

    def test_records_the_canonical_url_it_was_given(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        assert manifest.source.canonical_url == CANONICAL_URL
        assert manifest.source.provider == "printables"

    def test_selects_every_file_it_captured(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        assert list(manifest.selected_ids) == [file.id for file in manifest.files]

    def test_records_the_source_item_id(self) -> None:
        manifest = resolvers.parse_printables_capture(PRINTABLES_PAYLOAD, CANONICAL_URL)

        # The id is what lets a later re-import recognise the same model.
        assert manifest.source.source_item_id == "3161"

    def test_reads_a_title_reported_under_the_title_key(self) -> None:
        manifest = capture(title="Benchy")

        assert manifest.source.fields["title"].value == "Benchy"

    def test_reads_a_creator_reported_under_the_creator_key(self) -> None:
        manifest = capture(creator={"name": "Maker"})

        assert manifest.source.fields["creator_name"].value == "Maker"

    @pytest.mark.parametrize("key", ["handle", "name", "username"])
    def test_reads_every_key_printables_uses_for_a_creator_name(self, key: str) -> None:
        manifest = capture(user={key: "Maker"})

        assert manifest.source.fields["creator_name"].value == "Maker"

    def test_stringifies_a_numeric_creator_id(self) -> None:
        manifest = capture(user={"id": 42})

        assert manifest.source.fields["creator_id"].value == "42"

    @pytest.mark.parametrize("key", ["url", "profileUrl", "profile_url"])
    def test_reads_every_key_printables_uses_for_a_creator_url(self, key: str) -> None:
        manifest = capture(user={key: "https://p.test/@m"})

        assert manifest.source.fields["creator_url"].value == "https://p.test/@m"

    def test_reads_a_license_reported_as_a_plain_string(self) -> None:
        manifest = capture(license="CC-BY-4.0")

        # The licence governs whether the model may be shared onward, so a
        # shape change here must not silently drop it.
        assert manifest.source.fields["license_code"].value == "CC-BY-4.0"

    @pytest.mark.parametrize("key", ["code", "name", "slug"])
    def test_reads_every_key_printables_uses_for_a_license_code(self, key: str) -> None:
        manifest = capture(license={key: "CC0"})

        assert manifest.source.fields["license_code"].value == "CC0"

    def test_reads_an_attribution_reported_under_the_camel_case_key(self) -> None:
        manifest = capture(attributionText="By Maker")

        assert manifest.source.fields["attribution_text"].value == "By Maker"

    def test_omits_a_field_the_payload_does_not_carry(self) -> None:
        manifest = capture(name="Benchy")

        # An absent field is absent, not an empty string: the UI distinguishes
        # "no licence stated" from "licence blank".
        assert "license_code" not in manifest.source.fields

    def test_ignores_a_creator_that_is_not_an_object(self) -> None:
        manifest = capture(user="creativetools")

        assert "creator_name" not in manifest.source.fields

    def test_ignores_a_license_url_when_the_license_is_a_plain_string(self) -> None:
        manifest = capture(license="CC0")

        assert "license_url" not in manifest.source.fields

    def test_records_no_item_id_when_the_payload_reports_an_empty_one(self) -> None:
        manifest = resolvers.parse_printables_capture(
            {"data": {"print": {"id": "", "stls": [{"id": 7, "name": "part.stl"}]}}},
            CANONICAL_URL,
        )

        # An empty id is no id. Persisting `""` would make a later re-import
        # match every other capture that also failed to report one.
        assert manifest.source.source_item_id is None

    def test_refuses_a_model_page_with_nothing_downloadable(self) -> None:
        with pytest.raises(CaptureContractError):
            resolvers.parse_printables_capture(
                {"data": {"print": {"id": 3161, "name": "Benchy"}}}, CANONICAL_URL
            )

    def test_refuses_a_payload_with_no_print_object(self) -> None:
        with pytest.raises(CaptureContractError):
            resolvers.parse_printables_capture({"data": {}}, CANONICAL_URL)

    def test_refuses_a_payload_that_is_not_a_mapping(self) -> None:
        with pytest.raises(CaptureContractError):
            resolvers.parse_printables_capture("<html>error</html>", CANONICAL_URL)

    def test_refuses_a_print_object_of_the_wrong_type(self) -> None:
        with pytest.raises(CaptureContractError):
            resolvers.parse_printables_capture(
                {"data": {"print": "unavailable"}}, CANONICAL_URL
            )


class TestMakerworldInstanceId:
    def test_prefers_the_default_instance(self) -> None:
        assert resolvers.makerworld_instance_id({"defaultInstanceId": 99}) == "99"

    def test_falls_back_to_the_first_listed_instance(self) -> None:
        assert resolvers.makerworld_instance_id({"instances": [{"id": 55}]}) == "55"

    def test_skips_an_instance_with_no_id(self) -> None:
        assert resolvers.makerworld_instance_id({"instances": [{}, "bad"]}) is None

    def test_returns_nothing_for_a_design_that_is_not_an_object(self) -> None:
        assert resolvers.makerworld_instance_id("bad") is None


class TestMakerworldCollectionTitle:
    def test_reads_the_favorite_title(self) -> None:
        next_data = {"props": {"pageProps": {"favorite": {"title": "Samples"}}}}

        assert resolvers.makerworld_collection_title(next_data, "5") == "Samples"

    def test_reads_a_collection_title(self) -> None:
        next_data = {"props": {"pageProps": {"collection": {"title": "Samples"}}}}

        assert resolvers.makerworld_collection_title(next_data, "5") == "Samples"

    def test_reads_a_title_reported_as_a_name(self) -> None:
        next_data = {"props": {"pageProps": {"favorite": {"name": "Samples"}}}}

        assert resolvers.makerworld_collection_title(next_data, "5") == "Samples"

    def test_falls_back_to_a_name_built_from_the_id(self) -> None:
        # The collection still has to be importable under some name.
        assert resolvers.makerworld_collection_title({}, "5") == "Collection 5"

    def test_falls_back_when_the_hydration_data_is_the_wrong_shape(self) -> None:
        assert resolvers.makerworld_collection_title({"props": "bad"}, "5") == (
            "Collection 5"
        )


class TestMakerworldCollectionMembers:
    def test_reads_a_design_listed_directly(self) -> None:
        next_data = {"props": {"pageProps": {"designs": [{"id": 1, "title": "A"}]}}}

        assert resolvers.makerworld_collection_members(next_data) == [
            resolvers.CollectionMember(
                page_url="https://makerworld.com/en/models/1", title="A", source_id="1"
            )
        ]

    def test_reads_a_design_nested_under_a_design_key(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "designs": [{"design": {"designId": 2, "designTitle": "B"}}]
                }
            }
        }

        assert [
            m.source_id for m in resolvers.makerworld_collection_members(next_data)
        ] == ["2"]

    def test_lists_each_design_once(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "designs": [{"id": 1, "title": "A"}, {"id": 1, "title": "again"}]
                }
            }
        }

        # MakerWorld's hydration JSON repeats a design across several lists, and
        # a duplicate would import the same model twice.
        assert len(resolvers.makerworld_collection_members(next_data)) == 1

    def test_skips_an_entry_that_is_not_an_object(self) -> None:
        next_data = {"props": {"pageProps": {"designs": ["bad", {"id": 1}]}}}

        assert len(resolvers.makerworld_collection_members(next_data)) == 1

    def test_skips_an_entry_with_no_design_id(self) -> None:
        next_data = {"props": {"pageProps": {"designs": [{"title": "A"}]}}}

        assert resolvers.makerworld_collection_members(next_data) == []

    def test_names_a_design_by_its_id_when_it_has_no_title(self) -> None:
        next_data = {"props": {"pageProps": {"designs": [{"id": 1}]}}}

        assert resolvers.makerworld_collection_members(next_data)[0].title == "1"

    def test_finds_designs_in_a_nested_list(self) -> None:
        next_data = {
            "props": {"pageProps": {"data": {"modelList": [{"id": 3, "title": "C"}]}}}
        }

        # The list's key and depth move between MakerWorld releases, so the walk
        # is by key hint rather than by a fixed path.
        assert [
            m.source_id for m in resolvers.makerworld_collection_members(next_data)
        ] == ["3"]

    def test_ignores_a_list_whose_key_names_nothing_design_like(self) -> None:
        next_data = {"props": {"pageProps": {"breadcrumbs": [{"id": 4}]}}}

        assert resolvers.makerworld_collection_members(next_data) == []

    def test_returns_nothing_when_the_hydration_data_is_the_wrong_shape(self) -> None:
        assert resolvers.makerworld_collection_members({}) == []
