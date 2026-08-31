from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import printstash_core.mesh.preview_profile as profile_module
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE


def test_canonical_thumbnail_profile_is_stable() -> None:
    assert PREVIEW_PROFILE.version == 1
    assert PREVIEW_PROFILE.aspect_ratio == (4, 3)
    assert PREVIEW_PROFILE.margin_fraction == 0.10
    assert PREVIEW_PROFILE.hero_azimuth_degrees == -35.0
    assert PREVIEW_PROFILE.hero_elevation_degrees == 18.0
    assert PREVIEW_PROFILE.flat_tilt_degrees == 25.0
    assert PREVIEW_PROFILE.supersample_for(320) == 2
    assert PREVIEW_PROFILE.supersample_for(640) == 2
    assert PREVIEW_PROFILE.supersample_for(1280) == 1


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


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: profile_module._mapping([], "root"), "preview_profile_invalid_root"),
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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("aspectRatio", [4]),
        lambda payload: payload.__setitem__("marginFraction", 0.5),
    ],
)
def test_profile_structure_rejects_invalid_recipe(monkeypatch, mutate) -> None:
    manifest = json.loads(
        (Path(profile_module.__file__).with_name("preview_profile.json")).read_text()
    )
    mutate(manifest)
    encoded = json.dumps(manifest).encode()

    class Resource:
        def joinpath(self, _name: str):
            return self

        def read_bytes(self) -> bytes:
            return encoded

    monkeypatch.setattr(profile_module, "files", lambda _package: Resource())

    with pytest.raises(ValueError, match="preview_profile_invalid"):
        profile_module._load_profile()
