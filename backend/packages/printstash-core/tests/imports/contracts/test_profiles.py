"""Profiles retain provider/file estimates without widening the V2 boundary."""

import json

import pytest

from printstash_core.imports.contracts import (
    MAX_MANIFEST_BYTES,
    CaptureContractError,
    CaptureManifestV2,
    CaptureManifestV3,
    CaptureProfile,
)

from .test_manifest import manifest_dict


def profile_manifest():
    return manifest_dict(
        schema_version=3,
        profiles={
            "source-file-id": {
                "id": "profile-1",
                "name": "Two plates",
                "origin": "provider",
                "author": "Another creator",
                "estimated_seconds": 7200,
                "filament_grams": 123.5,
                "plate_count": 2,
            }
        },
    )


class TestCaptureProfiles:
    def test_profile_round_trip(self):
        from printstash_core.imports import parse_capture_manifest

        raw = profile_manifest()
        manifest = parse_capture_manifest(raw)
        assert manifest.to_dict()["profiles"] == raw["profiles"]
        assert manifest.profiles["source-file-id"].origin == "provider"

    def test_v2_rejects_profiles(self):
        with pytest.raises(CaptureContractError):
            CaptureManifestV2.from_dict(profile_manifest())

    def test_manifest_dispatch_preserves_v2(self):
        from printstash_core.imports import parse_capture_manifest

        assert parse_capture_manifest(manifest_dict()).schema_version == 2

    @pytest.mark.parametrize(
        "field,value",
        [
            ("cookies", "secret"),
            ("download_url", "https://cdn.test/?token=secret"),
            ("origin", "measured"),
            ("estimated_seconds", -1),
            ("estimated_seconds", True),
            ("filament_grams", float("nan")),
            ("plate_count", 1.5),
            ("author", "<script>bad</script>"),
            ("creator_relationship", "probably_creator"),
        ],
    )
    def test_refuses_unsafe_or_invented_profile_facts(self, field, value):
        from printstash_core.imports import parse_capture_manifest

        raw = profile_manifest()
        raw["profiles"]["source-file-id"][field] = value
        with pytest.raises(CaptureContractError):
            parse_capture_manifest(raw)

    def test_profile_must_belong_to_a_selected_file(self):
        from printstash_core.imports import parse_capture_manifest

        raw = profile_manifest()
        raw["profiles"]["unknown"] = raw["profiles"].pop("source-file-id")
        with pytest.raises(CaptureContractError):
            parse_capture_manifest(raw)

    def test_preserves_file_profile_metadata(self):
        raw = {
            "id": "project-1",
            "name": "Draft",
            "origin": "file",
            "printer": "Example printer",
            "creator_relationship": "other",
            "materials": ["PLA", "PETG"],
        }

        profile = CaptureProfile.from_dict(raw)

        assert profile.to_dict() == raw

    def test_leaves_unavailable_profile_facts_absent(self):
        raw = {"id": "project-1", "name": "Draft", "origin": "provider"}

        profile = CaptureProfile.from_dict(raw)

        assert profile.to_dict() == raw

    @pytest.mark.parametrize(
        "materials",
        [pytest.param("PLA", id="scalar"), pytest.param(["PLA"] * 65, id="too-many")],
    )
    def test_refuses_malformed_materials(self, materials):
        raw = {
            "id": "project-1",
            "name": "Draft",
            "origin": "file",
            "materials": materials,
        }

        with pytest.raises(CaptureContractError, match="invalid profile.materials"):
            CaptureProfile.from_dict(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(manifest_dict(), id="version-two"),
            pytest.param(manifest_dict(schema_version=3), id="missing-profiles"),
        ],
    )
    def test_requires_the_v3_envelope(self, raw):
        with pytest.raises(CaptureContractError, match="version 3 capture"):
            CaptureManifestV3.from_dict(raw)

    def test_bounds_the_entire_profile_manifest(self):
        files = [
            {
                "id": str(index),
                "name": f"part-{index}.3mf",
                "file_type": "3mf",
                "size": 100,
            }
            for index in range(40)
        ]
        raw = manifest_dict(
            schema_version=3,
            files=files,
            selected_ids=[file["id"] for file in files],
            profiles={
                file["id"]: {
                    "id": file["id"],
                    "name": "Draft",
                    "origin": "provider",
                    "materials": ["A" * 128] * 64,
                }
                for file in files
            },
        )
        assert len(json.dumps(raw).encode()) > MAX_MANIFEST_BYTES

        with pytest.raises(CaptureContractError, match="manifest exceeds maximum size"):
            CaptureManifestV3.from_dict(raw)
