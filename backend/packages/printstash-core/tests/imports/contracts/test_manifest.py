"""The boundary every externally captured model must pass through.

This module is the only thing standing between a third-party website's JSON and
the PrintStash database. Everything it validates is untrusted: the provider
chose the strings, and in the browser-extension flow so, indirectly, did whoever
authored the page. So the contract is deliberately strict rather than tolerant —
unknown keys are an error, not ignored; a value that is nearly right is refused,
not coerced.

The specific harms it exists to prevent:

- **A signed URL becoming durable.** A download link carries a credential in its
  query string. `ResolvedAsset` holds it in memory for the length of one
  download, and it must never appear in a manifest — which is why queries and
  fragments are stripped from every URL that *is* persisted.
- **A hostile host wearing a provider's name.** The canonical URL is the
  capture's identity, and `provider` claims which site it came from. Binding the
  two is what stops a manifest that says "printables" while pointing at
  `printables.com.attacker.test`. MakerWorld is the awkward case: it genuinely
  serves pages from first-party subdomains, so the check is exact-or-subdomain,
  never a suffix match.
- **Path confusion.** An encoded separator or a dot segment can be read one way
  here and another way by a browser or the provider's own server, which makes two
  different URLs look like one capture. Anything whose decoded form differs from
  what was validated is refused outright.
- **Injected markup and control data** in text that the UI renders and that
  round-trips through JSON.
- **Unbounded growth.** Every string has a length cap and the whole manifest has
  a byte cap, because these rows are written by a remote party.

The regression to fear is silent widening: a validator that starts accepting
something it used to refuse. Most rows here are therefore refusals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from printstash_core.imports.contracts import (
    MAX_MANIFEST_BYTES,
    CaptureContractError,
    CapturedField,
    CaptureFile,
    CaptureManifestV2,
    CaptureSource,
    ResolvedAsset,
    StagedAsset,
)
from printstash_core.imports.resolvers import parse_printables_capture

from ...paths import FIXTURES_DIR

FIXTURES = FIXTURES_DIR / "printables"
# Obviously fake: these appear only to prove they are discarded.
SIGNED_URL = "https://cdn.test/download?token=not-a-real-token"


def manifest_dict(**overrides: Any) -> dict[str, Any]:
    """A complete, valid manifest — the base every refusal row perturbs."""

    payload: dict[str, Any] = {
        "schema_version": 2,
        "kind": "model_files",
        "source": source_dict(),
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
    payload.update(overrides)
    return payload


def source_dict(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
            "license_text": {"value": "Attribution required", "origin": "confirmed"},
            "attribution_text": {"value": "By Creator", "origin": "confirmed"},
        },
    }
    payload.update(overrides)
    return payload


def with_field(name: str, value: Any, origin: str = "confirmed") -> dict[str, Any]:
    """A manifest carrying exactly one captured field."""

    return manifest_dict(
        source=source_dict(fields={name: {"value": value, "origin": origin}})
    )


class TestCapturedField:
    def test_keeps_a_confirmed_value(self) -> None:
        field = CapturedField.from_dict(
            {"value": "Example", "origin": "confirmed"}, "title"
        )

        assert field == CapturedField(value="Example", origin="confirmed")

    def test_keeps_an_inferred_value(self) -> None:
        # `inferred` is what the UI marks for review; losing the distinction
        # would present a guess as the provider's own statement.
        field = CapturedField.from_dict(
            {"value": "Guess", "origin": "inferred"}, "title"
        )

        assert field.origin == "inferred"

    def test_refuses_an_origin_it_does_not_know(self) -> None:
        with pytest.raises(CaptureContractError):
            CapturedField.from_dict({"value": "x", "origin": "scraped"}, "title")

    def test_refuses_an_unknown_key(self) -> None:
        with pytest.raises(CaptureContractError):
            CapturedField.from_dict(
                {"value": "x", "origin": "confirmed", "extra": 1}, "title"
            )

    def test_refuses_a_missing_key(self) -> None:
        with pytest.raises(CaptureContractError):
            CapturedField.from_dict({"value": "x"}, "title")

    def test_refuses_a_field_that_is_not_an_object(self) -> None:
        with pytest.raises(CaptureContractError):
            CapturedField.from_dict("Example", "title")

    def test_normalizes_text_before_hashing(self) -> None:
        field = CapturedField.from_dict(
            {"value": "é\r\nsecond", "origin": "confirmed"}, "title"
        )

        # One spelling in the database, so search and dedupe behave.
        assert field.value == "é\nsecond"

    def test_round_trips_to_the_persisted_shape(self) -> None:
        data = {"value": "Example", "origin": "confirmed"}

        assert CapturedField.from_dict(data, "title").to_dict() == data


class TestCaptureSource:
    def test_strips_the_query_from_the_canonical_url(self) -> None:
        source = CaptureSource.from_dict(source_dict())

        assert source.canonical_url == "https://www.printables.com/model/123-example"

    def test_keeps_the_declared_tags(self) -> None:
        source = CaptureSource.from_dict(source_dict())

        assert source.tags == ("calibration", "functional")

    def test_defaults_to_no_tags(self) -> None:
        # Tags are the one optional key; a sparse capture omits them.
        payload = source_dict()
        del payload["tags"]

        assert CaptureSource.from_dict(payload).tags == ()

    def test_accepts_a_capture_with_no_item_id(self) -> None:
        source = CaptureSource.from_dict(source_dict(source_item_id=None))

        # Some providers do not expose one; the capture is still importable,
        # just not re-matchable later.
        assert source.source_item_id is None

    def test_accepts_a_revision(self) -> None:
        source = CaptureSource.from_dict(source_dict(source_revision="rev-2"))

        assert source.source_revision == "rev-2"

    def test_refuses_a_duplicate_tag(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(source_dict(tags=["a", "a"]))

    def test_refuses_more_tags_than_the_cap(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(source_dict(tags=[f"t{i}" for i in range(101)]))

    def test_refuses_tags_that_are_not_a_list(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(source_dict(tags="calibration"))

    def test_refuses_an_unknown_source_key(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(source_dict(cookies={"session": "x"}))

    def test_refuses_a_missing_source_key(self) -> None:
        payload = source_dict()
        del payload["adapter_version"]

        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(payload)

    def test_refuses_an_unknown_captured_field(self) -> None:
        # The field allowlist is what keeps provider payload sprawl out of the
        # database; an unknown name means the adapter changed unreviewed.
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(
                source_dict(
                    fields={"internal_notes": {"value": "x", "origin": "confirmed"}}
                )
            )

    def test_refuses_a_fields_value_that_is_not_an_object(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(source_dict(fields="none"))

    def test_refuses_a_source_that_is_not_an_object(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict("printables")

    def test_round_trips_to_the_persisted_shape(self) -> None:
        source = CaptureSource.from_dict(source_dict())

        assert CaptureSource.from_dict(source.to_dict()) == source


class TestCaptureFile:
    def test_keeps_the_file_descriptor(self) -> None:
        file = CaptureFile.from_dict(
            {"id": "7", "name": "part.stl", "file_type": "stl", "size": 10}
        )

        assert file == CaptureFile(id="7", name="part.stl", file_type="stl", size=10)

    def test_accepts_an_unknown_size(self) -> None:
        # Providers often omit it, and a missing size is not a reason to refuse
        # an otherwise valid file.
        file = CaptureFile.from_dict(
            {"id": "7", "name": "a.stl", "file_type": "stl", "size": None}
        )

        assert file.size is None

    @pytest.mark.parametrize("size", [-1, 1.5, "10", True])
    def test_refuses_a_size_that_is_not_a_count_of_bytes(self, size: Any) -> None:
        with pytest.raises(CaptureContractError):
            CaptureFile.from_dict(
                {"id": "7", "name": "a.stl", "file_type": "stl", "size": size}
            )

    def test_refuses_an_unknown_key(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureFile.from_dict(
                {
                    "id": "7",
                    "name": "a.stl",
                    "file_type": "stl",
                    "size": None,
                    "url": SIGNED_URL,
                }
            )

    def test_refuses_an_empty_name(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureFile.from_dict(
                {"id": "7", "name": "", "file_type": "stl", "size": None}
            )

    def test_round_trips_to_the_persisted_shape(self) -> None:
        data = {"id": "7", "name": "a.stl", "file_type": "stl", "size": 10}

        assert CaptureFile.from_dict(data).to_dict() == data


class TestCaptureManifestV2:
    def test_parses_a_complete_manifest(self) -> None:
        manifest = CaptureManifestV2.from_dict(manifest_dict())

        assert manifest.source.provider == "printables"
        assert manifest.selected_ids == ("source-file-id",)

    def test_round_trips_to_the_persisted_shape(self) -> None:
        manifest = CaptureManifestV2.from_dict(manifest_dict())

        # Manifests are stored and re-read; a lossy round trip would mean a
        # capture drifts every time it is loaded.
        assert CaptureManifestV2.from_dict(manifest.to_dict()) == manifest

    def test_never_serializes_a_download_url(self) -> None:
        manifest = CaptureManifestV2.from_dict(manifest_dict())

        assert "download_url" not in json.dumps(manifest.to_dict())

    def test_refuses_an_unknown_top_level_key(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(manifest_dict(download_url=SIGNED_URL))

    def test_refuses_a_missing_top_level_key(self) -> None:
        payload = manifest_dict()
        del payload["selected_ids"]

        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(payload)

    @pytest.mark.parametrize(
        "overrides", [{"schema_version": 1}, {"kind": "collection"}]
    )
    def test_refuses_a_schema_it_does_not_implement(
        self, overrides: dict[str, Any]
    ) -> None:
        # A future schema must fail loudly rather than be half-read by this
        # version's rules.
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(manifest_dict(**overrides))

    @pytest.mark.parametrize("files", [[], "none", None])
    def test_refuses_a_capture_with_no_files(self, files: Any) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(manifest_dict(files=files))

    def test_refuses_two_files_sharing_an_id(self) -> None:
        # The id is how a selection names a file; a duplicate makes the
        # selection ambiguous.
        duplicate = {
            "id": "source-file-id",
            "name": "b.stl",
            "file_type": "stl",
            "size": None,
        }

        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(
                manifest_dict(files=[*manifest_dict()["files"], duplicate])
            )

    @pytest.mark.parametrize("selected", [[], "source-file-id", None])
    def test_refuses_a_capture_with_nothing_selected(self, selected: Any) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(manifest_dict(selected_ids=selected))

    def test_refuses_a_selection_naming_a_file_that_is_not_present(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(manifest_dict(selected_ids=["absent"]))

    def test_refuses_a_repeated_selection(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(
                manifest_dict(selected_ids=["source-file-id", "source-file-id"])
            )

    def test_refuses_a_manifest_over_the_byte_cap(self) -> None:
        # Every field here is within its own cap; together they are not. These
        # bytes are chosen by a remote party and stored once per model, so the
        # whole-manifest ceiling is a separate limit from the per-field ones.
        fields = {
            name: {"value": "a" * length, "origin": "confirmed"}
            for name, length in (
                ("instructions", 130_000),
                ("description", 65_000),
                ("license_text", 65_000),
                ("attribution_text", 65_000),
            )
        }

        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(
                manifest_dict(source=source_dict(fields=fields))
            )

    def test_refuses_a_single_field_over_its_own_cap(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(
                with_field("description", "a" * (MAX_MANIFEST_BYTES + 1))
            )

    def test_refuses_a_manifest_that_is_not_an_object(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict([manifest_dict()])


class TestCapturedTextValidation:
    @pytest.mark.parametrize(
        "value",
        [
            "<script>alert(1)</script>",
            "Example <b>bold</b>",
            "</div>",
            "<img src=x onerror=alert(1)>",
        ],
    )
    def test_refuses_markup_in_a_captured_value(self, value: str) -> None:
        # The UI renders these strings, and they round-trip through JSON into
        # the extension. Refusing markup at the boundary is one rule instead of
        # an escaping obligation at every render site.
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field("title", value))

    @pytest.mark.parametrize("value", ["a\x00b", "a\x07b", "a\x1bb"])
    def test_refuses_control_data_in_a_captured_value(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field("title", value))

    def test_allows_a_newline_in_a_captured_value(self) -> None:
        # Descriptions and instructions are genuinely multi-line.
        manifest = CaptureManifestV2.from_dict(
            with_field("description", "line one\nline two")
        )

        assert manifest.source.fields["description"].value == "line one\nline two"

    def test_refuses_an_empty_captured_value(self) -> None:
        # An empty string is a transport placeholder, not a captured fact;
        # storing it would fabricate capture history.
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field("title", ""))

    def test_refuses_a_captured_value_that_is_not_a_string(self) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field("title", 123))

    def test_refuses_a_title_over_its_own_length_cap(self) -> None:
        # Each field has its own cap: a 600-character title is wrong even
        # though a 600-character description is fine.
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field("title", "a" * 513))

    def test_accepts_a_description_at_its_larger_cap(self) -> None:
        manifest = CaptureManifestV2.from_dict(with_field("description", "a" * 513))

        assert len(manifest.source.fields["description"].value) == 513

    @pytest.mark.parametrize("field_name", ["published_at", "updated_at"])
    def test_accepts_an_iso_8601_timestamp(self, field_name: str) -> None:
        manifest = CaptureManifestV2.from_dict(
            with_field(field_name, "2026-01-01T00:00:00Z")
        )

        assert manifest.source.fields[field_name].value == "2026-01-01T00:00:00Z"

    @pytest.mark.parametrize("field_name", ["published_at", "updated_at"])
    def test_refuses_a_timestamp_that_is_not_iso_8601(self, field_name: str) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field(field_name, "yesterday"))

    @pytest.mark.parametrize("field_name", ["creator_url", "license_url"])
    def test_strips_the_query_from_a_captured_url(self, field_name: str) -> None:
        manifest = CaptureManifestV2.from_dict(
            with_field(field_name, "https://example.test/a?token=secret")
        )

        assert manifest.source.fields[field_name].value == "https://example.test/a"

    @pytest.mark.parametrize("field_name", ["creator_url", "license_url"])
    def test_refuses_a_captured_url_that_is_not_absolute_http(
        self, field_name: str
    ) -> None:
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(with_field(field_name, "javascript:alert(1)"))


class TestResolvedAsset:
    def test_holds_the_download_descriptor_beside_the_manifest(self) -> None:
        manifest = CaptureManifestV2.from_dict(manifest_dict())

        resolved = ResolvedAsset(
            manifest=manifest,
            source_selection_id="123:file-7",
            source_file_id="file-7",
            source_filename="cube.stl",
            download_url=SIGNED_URL,
            source_item_id="123",
        )

        # The signed URL lives here, for the length of one download, and
        # nowhere in the manifest it points at.
        assert resolved.download_url == SIGNED_URL
        assert SIGNED_URL not in json.dumps(resolved.manifest.to_dict())


class TestStagedAsset:
    def test_exposes_the_selection_id_of_the_asset_it_staged(self) -> None:
        staged = self.staged()

        assert staged.source_selection_id == "123:file-7"

    def test_exposes_the_manifest_of_the_asset_it_staged(self) -> None:
        staged = self.staged()

        # Callers hold a StagedAsset after download and still need the capture
        # metadata to write the library row.
        assert staged.manifest.source.source_item_id == "123"

    def staged(self) -> StagedAsset:
        return StagedAsset(
            resolved=ResolvedAsset(
                manifest=CaptureManifestV2.from_dict(manifest_dict()),
                source_selection_id="123:file-7",
                source_file_id="file-7",
                source_filename="cube.stl",
                download_url=SIGNED_URL,
                source_item_id="123",
            ),
            staged_path=Path("/tmp/staged"),
            result_key="self",
            blob_sha256="a" * 64,
        )


class TestPrintablesFixtureCapture:
    def test_parses_a_recorded_provider_response_into_the_persisted_shape(
        self,
    ) -> None:
        payload = json.loads((FIXTURES / "single_model.json").read_text())

        manifest = parse_printables_capture(
            payload,
            "https://www.printables.com/model/123-example?utm_source=extension"
            "&token=secret",
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
                    "description": {
                        "value": "A calibration model",
                        "origin": "confirmed",
                    },
                    "instructions": {"value": "Print in PLA", "origin": "confirmed"},
                    "creator_name": {
                        "value": "PrintStash Tester",
                        "origin": "confirmed",
                    },
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

    def test_drops_the_provider_state_the_recorded_response_carries(self) -> None:
        payload = json.loads((FIXTURES / "single_model.json").read_text())

        manifest = parse_printables_capture(
            payload, "https://www.printables.com/model/123-example"
        )

        # The fixture deliberately contains a signature, an HTML blob, and an
        # unexpected key; none may survive into the persisted manifest.
        serialized = json.dumps(manifest.to_dict())
        assert "signature" not in serialized
        assert "<html" not in serialized.lower()
        assert "unexpected" not in serialized

    def test_accepts_a_recorded_response_with_no_item_id(self) -> None:
        payload = json.loads((FIXTURES / "missing_id.json").read_text())

        manifest = parse_printables_capture(
            payload, "https://www.printables.com/model/123-example"
        )

        assert manifest.source.source_item_id is None
