"""Defends ``test_capture_manifest_v2_is_strict_and_strips_url_queries`` behavior for the ``contracts`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from printstash_core.imports.contracts import (
    CaptureContractError,
    CaptureManifestV2,
    ResolvedAsset,
    StagedAsset,
)
from printstash_core.imports.resolvers import parse_printables_capture

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "printables"


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "kind": "model_files",
        "source": {
            "provider": "printables",
            "canonical_url": "https://www.printables.com/model/123-example?token=secret",
            "source_item_id": "123",
            "source_revision": None,
            "adapter_version": "printables-v1",
            "tags": ["calibration", "functional"],
            "fields": {
                "title": {"value": "Example", "origin": "confirmed"},
                "description": {"value": "A description", "origin": "confirmed"},
                "instructions": {"value": "Print carefully", "origin": "confirmed"},
                "creator_name": {"value": "Creator", "origin": "confirmed"},
                "creator_id": {"value": "creator-1", "origin": "confirmed"},
                "creator_url": {
                    "value": "https://www.printables.com/@creator",
                    "origin": "confirmed",
                },
                "license_code": {"value": "CC-BY-4.0", "origin": "confirmed"},
                "license_url": {
                    "value": "https://creativecommons.org/licenses/by/4.0/",
                    "origin": "confirmed",
                },
                "license_text": {
                    "value": "Attribution required",
                    "origin": "confirmed",
                },
                "attribution_text": {"value": "By Creator", "origin": "confirmed"},
            },
        },
        "files": [
            {
                "id": "source-file-id",
                "name": "part.stl",
                "file_type": "stl",
                "size": 12345,
            }
        ],
        "selected_ids": ["source-file-id"],
    }


def test_capture_manifest_v2_is_strict_and_strips_url_queries() -> None:
    manifest = CaptureManifestV2.from_dict(_manifest())

    assert manifest.to_dict() == {
        **_manifest(),
        "source": {
            **_manifest()["source"],  # type: ignore[dict-item]
            "canonical_url": "https://www.printables.com/model/123-example",
        },
    }

    for forbidden in (
        {**_manifest(), "unexpected": True},
        {
            **_manifest(),
            "files": [
                {
                    "id": "f",
                    "name": "part.stl",
                    "file_type": "stl",
                    "url": "https://signed.test/x?token=secret",
                }
            ],
        },
        {**_manifest(), "selected_ids": ["missing"]},
    ):
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(forbidden)


def test_capture_manifest_v2_rejects_oversized_and_html_values() -> None:
    oversized = _manifest()
    oversized["source"] = {
        **oversized["source"],  # type: ignore[dict-item]
        "fields": {
            "description": {"value": "x" * (64 * 1024 + 1), "origin": "confirmed"}
        },
    }
    html = _manifest()
    html["source"] = {
        **html["source"],  # type: ignore[dict-item]
        "fields": {
            "title": {"value": "<p>not captured HTML</p>", "origin": "confirmed"}
        },
    }
    empty = _manifest()
    empty["source"] = {
        **empty["source"],  # type: ignore[dict-item]
        "fields": {"title": {"value": "", "origin": "confirmed"}},
    }

    for invalid in (oversized, html, empty):
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(invalid)


def test_capture_manifest_v2_validates_tags_and_datetime_fields() -> None:
    valid = _manifest()
    valid["source"] = {
        **valid["source"],  # type: ignore[dict-item]
        "tags": ["Caf\u00e9", "print-ready"],
        "fields": {
            "published_at": {"value": "2026-08-24T12:34:56Z", "origin": "confirmed"},
            "updated_at": {"value": "2026-08-24T12:34:56+00:00", "origin": "inferred"},
        },
    }
    manifest = CaptureManifestV2.from_dict(valid)
    assert manifest.source.tags == ("Caf\u00e9", "print-ready")
    assert manifest.source.fields["published_at"].value.endswith("Z")

    invalid_tags = _manifest()
    invalid_tags["source"] = {**invalid_tags["source"], "tags": ["x"] * 101}  # type: ignore[dict-item]
    invalid_datetime = _manifest()
    invalid_datetime["source"] = {
        **invalid_datetime["source"],  # type: ignore[dict-item]
        "fields": {"published_at": {"value": "yesterday", "origin": "confirmed"}},
    }
    for invalid in (invalid_tags, invalid_datetime):
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(invalid)


def test_capture_manifest_supports_url_fallback_identity_and_normalizes_safe_text() -> (
    None
):
    manifest_data = _manifest()
    manifest_data["source"] = {
        **manifest_data["source"],  # type: ignore[dict-item]
        "source_item_id": None,
        "fields": {"title": {"value": "Cafe\u0301\r\nBracket", "origin": "confirmed"}},
    }

    manifest = CaptureManifestV2.from_dict(manifest_data)

    assert manifest.source.source_item_id is None
    assert manifest.source.fields["title"].value == "Café\nBracket"

    unsafe = _manifest()
    unsafe["source"] = {
        **unsafe["source"],  # type: ignore[dict-item]
        "fields": {"title": {"value": "unsafe\x1bvalue", "origin": "confirmed"}},
    }
    with pytest.raises(CaptureContractError):
        CaptureManifestV2.from_dict(unsafe)


def test_resolved_and_staged_assets_keep_download_descriptor_out_of_manifest() -> None:
    asset = ResolvedAsset(
        manifest=CaptureManifestV2.from_dict(_manifest()),
        source_selection_id="123:file-7",
        source_file_id="file-7",
        source_filename="part.stl",
        download_url="https://cdn.example.test/part.stl?signature=secret",
        source_item_id="123",
    )
    staged = StagedAsset(
        resolved=asset,
        staged_path=Path("/tmp/part.stl"),
        result_key="self",
        blob_sha256="a" * 64,
    )

    assert staged.source_selection_id == "123:file-7"
    assert staged.manifest.source.source_item_id == "123"
    assert "download_url" not in CaptureManifestV2.from_dict(_manifest()).to_dict()
    assert staged.result_key == "self"


def test_printables_fixture_parser_returns_safe_typed_manifest() -> None:
    payload = json.loads((FIXTURES / "single_model.json").read_text())

    manifest = parse_printables_capture(
        payload,
        "https://www.printables.com/model/123-example?utm_source=extension&token=secret",
    )

    assert manifest.to_dict() == {
        "schema_version": 2,
        "kind": "model_files",
        "source": {
            "provider": "printables",
            "canonical_url": "https://www.printables.com/model/123-example",
            "source_item_id": "123",
            "source_revision": None,
            "adapter_version": "printables-v1",
            "tags": [],
            "fields": {
                "title": {"value": "Calibration Cube", "origin": "confirmed"},
                "description": {"value": "A calibration model", "origin": "confirmed"},
                "instructions": {"value": "Print in PLA", "origin": "confirmed"},
                "creator_name": {"value": "PrintStash Tester", "origin": "confirmed"},
                "creator_id": {"value": "99", "origin": "confirmed"},
                "creator_url": {
                    "value": "https://www.printables.com/@printstash-tester",
                    "origin": "confirmed",
                },
                "license_code": {"value": "CC-BY-4.0", "origin": "confirmed"},
                "license_url": {
                    "value": "https://creativecommons.org/licenses/by/4.0/",
                    "origin": "confirmed",
                },
                "license_text": {
                    "value": "Attribution required",
                    "origin": "confirmed",
                },
                "attribution_text": {
                    "value": "Designed by PrintStash Tester",
                    "origin": "confirmed",
                },
            },
        },
        "files": [
            {"id": "file-7", "name": "cube.stl", "file_type": "stl", "size": 123},
            {"id": "file-8", "name": "notes.txt", "file_type": "other", "size": 45},
        ],
        "selected_ids": ["file-7", "file-8"],
    }
    serialized = json.dumps(manifest.to_dict())
    assert "signature" not in serialized
    assert "<html" not in serialized.lower()
    assert "unexpected" not in serialized


def test_printables_fixture_parser_rejects_missing_identity() -> None:
    payload = json.loads((FIXTURES / "missing_id.json").read_text())

    manifest = parse_printables_capture(
        payload, "https://www.printables.com/model/123-example"
    )

    assert manifest.source.source_item_id is None
