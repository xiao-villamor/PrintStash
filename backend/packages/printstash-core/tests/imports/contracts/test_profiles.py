"""Profiles retain provider/file estimates without widening the V2 boundary."""

import pytest

from printstash_core.imports.contracts import CaptureContractError, CaptureManifestV2

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
