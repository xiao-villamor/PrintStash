"""Defend the canonical preview recipe and its generated web representation.

A failure means backend and frontend thumbnails can silently frame the same
model differently, so the manifest must remain the sole source of truth.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import printstash_core.mesh.preview_profile as profile_module
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE


class TestPreviewProfile:
    @staticmethod
    def test_studio_thumbnail_framing_is_stable() -> None:
        assert PREVIEW_PROFILE.version == 8
        assert PREVIEW_PROFILE.aspect_ratio == (4, 3)
        assert PREVIEW_PROFILE.margin_fraction == 0.10
        assert PREVIEW_PROFILE.hero_azimuth_degrees == -35.0
        assert PREVIEW_PROFILE.hero_elevation_degrees == 30.0
        assert PREVIEW_PROFILE.flat_tilt_degrees == 25.0
        assert PREVIEW_PROFILE.supersample_for(320) == 2
        assert PREVIEW_PROFILE.supersample_for(640) == 2
        assert PREVIEW_PROFILE.supersample_for(1280) == 1
        assert PREVIEW_PROFILE.encoding_method == 2
        assert PREVIEW_PROFILE.encoding_quality == 85
        assert PREVIEW_PROFILE.encoding_lossless is False
        assert PREVIEW_PROFILE.encoding_exact is False

    @staticmethod
    def test_studio_looks_have_the_canonical_palette() -> None:
        colours = [
            tuple(round(channel * 255) for channel in studio.albedo)
            for studio in profile_module.STUDIO_LOOKS.values()
        ]
        assert colours == [
            (159, 184, 218),
            (94, 139, 203),
            (142, 151, 166),
            (110, 115, 124),
            (205, 188, 168),
            (137, 173, 149),
            (203, 135, 112),
            (165, 139, 191),
        ]

    @staticmethod
    def test_generated_frontend_profile_matches_manifest() -> None:
        repository = Path(__file__).resolve().parents[5]
        package = repository / "backend/packages/printstash-core"
        script_path = package / "scripts/generate_preview_profile.py"
        generated_path = repository / "frontend/src/lib/thumbnail-profile.generated.ts"
        spec = importlib.util.spec_from_file_location(
            "generate_preview_profile", script_path
        )
        assert spec is not None and spec.loader is not None
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        manifest = generator.json.loads(generator.MANIFEST.read_text())

        assert generated_path.read_text() == generator.render_typescript(manifest)

    @staticmethod
    @pytest.mark.parametrize(
        ("call", "expected"),
        [
            (
                lambda: profile_module._mapping([], "root"),
                "preview_profile_invalid_root",
            ),
            (
                lambda: profile_module._number({"margin": True}, "margin"),
                "preview_profile_invalid_margin",
            ),
            (
                lambda: profile_module._integer({"version": 1.5}, "version"),
                "preview_profile_invalid_version",
            ),
        ],
    )
    def test_profile_scalar_validation_is_typed(call, expected: str) -> None:
        with pytest.raises(ValueError, match=expected):
            call()

    @staticmethod
    @pytest.mark.parametrize(
        "mutate",
        [
            lambda payload: payload.__setitem__("aspectRatio", [4]),
            lambda payload: payload.__setitem__("marginFraction", 0.5),
            lambda payload: payload["looks"]["sky"].__setitem__("albedo", [1, 2]),
            lambda payload: payload["looks"].pop("sand"),
            lambda payload: payload["looks"].__setitem__("other", {}),
            lambda payload: payload["looks"]["sky"].__setitem__("key", -1),
            lambda payload: payload["looks"]["slate"].__setitem__(
                "ambient", float("inf")
            ),
            lambda payload: payload["lighting"]["key"].__setitem__(
                "direction", [0, 0, 0]
            ),
            lambda payload: payload["lighting"]["fill"].__setitem__(
                "colour", [1, True, 1]
            ),
            lambda payload: payload["lighting"]["rim"].__setitem__("power", 0),
            lambda payload: payload["encoding"].__setitem__("method", -1),
            lambda payload: payload["encoding"].__setitem__("method", 7),
            lambda payload: payload["encoding"].__setitem__("quality", -1),
            lambda payload: payload["encoding"].__setitem__("quality", 101),
            lambda payload: payload["encoding"].__setitem__("quality", True),
            lambda payload: payload["encoding"].__setitem__("lossless", 1),
            lambda payload: payload["encoding"].__setitem__("exact", "false"),
        ],
        ids=[
            "aspect",
            "margin",
            "albedo",
            "missing-look",
            "unknown-look",
            "negative-key",
            "nonfinite-ambient",
            "zero-direction",
            "boolean-colour",
            "zero-power",
            "negative-method",
            "excess-method",
            "negative-quality",
            "excess-quality",
            "boolean-quality",
            "nonboolean-lossless",
            "nonboolean-exact",
        ],
    )
    def test_profile_structure_rejects_invalid_recipe(mutate) -> None:
        manifest = json.loads(
            (
                Path(profile_module.__file__).with_name("preview_profile.json")
            ).read_text()
        )
        mutate(manifest)
        encoded = json.dumps(manifest).encode()

        with pytest.raises(ValueError, match="preview_profile_invalid"):
            profile_module._parse_studio_profile(encoded, manifest)


class TestDecodeAppearance:
    def test_decodes_a_named_look(self):
        result = profile_module.decode_appearance({"look": "sand", "floor_grid": False})

        assert result == profile_module.PreviewAppearance(
            profile_module.PreviewLook.SAND, False
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"look": "sand"},
            {"look": "sand", "floor_grid": 0},
            {"look": "sand", "floor_grid": True, "extra": "unexpected"},
            {"look": "invalid", "floor_grid": True},
        ],
    )
    def test_rejects_an_invalid_appearance(self, payload):
        with pytest.raises(ValueError):
            profile_module.decode_appearance(payload)


class TestPreviewRasterizerRecipe:
    def test_studio_recipe_changes_with_appearance(self):
        original = profile_module.preview_rasterizer_recipe(matte=False)
        appearance = profile_module.PreviewAppearance(
            profile_module.PreviewLook.SAND, False
        )

        with profile_module.appearance_scope(appearance):
            changed = profile_module.preview_rasterizer_recipe(matte=False)

        assert changed != original
        assert changed.endswith("-sand-0")
        assert profile_module.preview_rasterizer_recipe(matte=False) == original

    def test_matte_recipe_is_independent_of_studio_appearance(self):
        original = profile_module.preview_rasterizer_recipe(matte=True)
        appearance = profile_module.PreviewAppearance(
            profile_module.PreviewLook.SAND, False
        )

        with profile_module.appearance_scope(appearance):
            assert profile_module.preview_rasterizer_recipe(matte=True) == original
