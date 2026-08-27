"""Defends ``test_resolver_rules_are_exported_from_imports_package`` behavior for the ``import_resolvers`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import json

import pytest
from printstash_core.imports import classify_page, resolvers


def test_resolver_rules_are_exported_from_imports_package() -> None:
    assert classify_page is resolvers.classify_page


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.printables.com/model/3161-3d-benchy", "printables"),
        ("https://makerworld.com/en/models/1123776-benchy", "makerworld"),
        ("https://assets.makerworld.com/en/models/1123776-benchy", "makerworld"),
        ("https://www.thingiverse.com/thing:763622/files", "thingiverse"),
        ("https://www.thingiverse.com/things/763622/files", "thingiverse"),
        ("https://files.printables.com/model/3161/file.stl", None),
        ("https://evilmakerworld.com/en/models/1123776", None),
        ("https://makerworld.com.attacker.test/models/1123776", None),
        ("not a url", None),
    ],
)
def test_classify_page(url: str, expected: str | None) -> None:
    assert resolvers.classify_page(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://printables.com/@u/collections/3525050", "printables"),
        ("https://makerworld.com/en/collections/5600774-slug", "makerworld"),
        ("https://x.makerworld.com/collections/5600774", "makerworld"),
        ("https://evilmakerworld.com/collections/5600774", None),
        ("https://printables.com/model/3525050", None),
    ],
)
def test_classify_collection(url: str, expected: str | None) -> None:
    assert resolvers.classify_collection(url) == expected


def test_page_and_collection_id_extractors_preserve_path_rules() -> None:
    assert resolvers.printables_id("https://printables.com/model/3161-x") == "3161"
    assert resolvers.makerworld_id("https://makerworld.com/en/models/12-x") == "12"
    assert resolvers.thingiverse_id("https://thingiverse.com/thing:763622") == "763622"
    assert resolvers.thingiverse_id("https://thingiverse.com/things/8/files") == "8"
    assert resolvers.collection_id("https://printables.com/collections/9-x") == "9"
    assert resolvers.collection_id("https://printables.com/model/9-x") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://cdn.test/model.stl", True),
        ("https://cdn.test/model.3MF?download=1", True),
        ("https://cdn.test/api/download/123", True),
        ("https://cdn.test/get?file=model.stl", False),
        ("https://cdn.test/image.png", False),
    ],
)
def test_download_detection(url: str, expected: bool) -> None:
    assert resolvers.looks_like_download(url) is expected


def test_first_download_url_prefers_keyed_url_over_bare_fallback() -> None:
    payload = {
        "files": ["https://cdn.test/fallback.stl"],
        "nested": {"downloadUrl": "https://cdn.test/preferred.zip"},
    }
    assert resolvers.first_download_url(payload) == "https://cdn.test/preferred.zip"
    assert resolvers.first_download_url({"url": "/relative.stl"}) is None


def test_challenge_and_next_data_detection() -> None:
    challenge = "<title>Just a moment...</title><div class='cf-chl'></div>"
    assert resolvers.looks_like_challenge(challenge) is True
    assert (
        resolvers.looks_like_challenge(
            '<script id="__NEXT_DATA__">{}</script> just a moment'
        )
        is False
    )

    payload = {"props": {"pageProps": {"ok": True}}}
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script>"
    )
    assert resolvers.extract_next_data(html) == payload
    assert (
        resolvers.extract_next_data("<script id='__NEXT_DATA__'>{bad}</script>") is None
    )


def test_printables_pack_and_link_payload_selection() -> None:
    packs = [{"id": 5, "fileType": "OTHER"}, {"id": 9, "fileType": "MODEL_FILES"}]
    assert resolvers.pick_printables_pack(packs) == "9"
    assert resolvers.pick_printables_pack([{"id": 7}]) == "7"
    assert resolvers.pick_printables_pack(None) is None

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
    assert resolvers.printables_link_from_output(payload) == "https://cdn.test/pack.zip"
    assert resolvers.printables_links_from_output(payload) == [
        "https://cdn.test/a.stl",
        "https://cdn.test/b.stl",
    ]


def test_printables_file_payload_normalization() -> None:
    files = resolvers.printables_files_from_print(
        {
            "stls": [
                {"id": 7, "name": "part.stl", "fileSize": 123},
                {"id": None, "name": "ignored.stl"},
            ],
            "gcodes": [{"id": "8", "name": "", "fileSize": "unknown"}],
            "slas": [],
            "otherFiles": [{"id": 9}],
        }
    )
    assert files == [
        resolvers.ModelFile("7", "part.stl", "stl", 123),
        resolvers.ModelFile("8", "8", "gcode", None),
        resolvers.ModelFile("9", "9", "other", None),
    ]


def test_makerworld_payload_selection_and_collection_normalization() -> None:
    assert resolvers.makerworld_instance_id({"defaultInstanceId": 99}) == "99"
    assert resolvers.makerworld_instance_id({"instances": [{"id": 55}]}) == "55"
    assert resolvers.makerworld_instance_id({"instances": [{}, "bad"]}) is None

    next_data = {
        "props": {
            "pageProps": {
                "favorite": {"title": "Samples"},
                "designs": [
                    "bad",
                    {"id": 1, "title": "A"},
                    {"id": 1, "title": "duplicate"},
                    {"design": {"designId": 2, "designTitle": "B"}},
                ],
            }
        }
    }
    assert resolvers.makerworld_collection_title(next_data, "5") == "Samples"
    assert resolvers.makerworld_collection_members(next_data) == [
        resolvers.CollectionMember(
            page_url="https://makerworld.com/en/models/1",
            title="A",
            source_id="1",
        ),
        resolvers.CollectionMember(
            page_url="https://makerworld.com/en/models/2",
            title="B",
            source_id="2",
        ),
    ]
    assert resolvers.makerworld_collection_title({}, "5") == "Collection 5"
    assert resolvers.makerworld_collection_members({}) == []
