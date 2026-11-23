#!/usr/bin/env python3
"""Generate the browser constants from the canonical preview recipe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[2]
MANIFEST = PACKAGE_ROOT / "src/printstash_core/mesh/preview_profile.json"
OUTPUT = REPOSITORY_ROOT / "frontend/src/lib/thumbnail-profile.generated.ts"


def render_typescript(profile: dict[str, object]) -> str:
    aspect = profile["aspectRatio"]
    hero = profile["hero"]
    flat = profile["flat"]
    supersampling = profile["supersampling"]
    encoding = profile["encoding"]
    assert isinstance(aspect, list)
    assert isinstance(hero, dict)
    assert isinstance(flat, dict)
    assert isinstance(supersampling, dict)
    assert isinstance(encoding, dict)
    looks = profile["looks"]
    assert isinstance(looks, dict)
    colour_properties = []
    for look, style in looks.items():
        albedo = style["albedo"]
        colour = "#" + "".join(f"{round(channel * 255):02X}" for channel in albedo)
        colour_properties.append(f"  {look}: {json.dumps(colour)},\n")
    lighting = profile["lighting"]
    assert isinstance(lighting, dict)

    def numbers(values: list[float]) -> str:
        return "[" + ", ".join(json.dumps(value) for value in values) + "]"

    studio_looks = "".join(
        f"    {look}: {{\n"
        f"      albedo: {numbers(style['albedo'])},\n"
        + "".join(
            f"      {name}: {style[name]},\n"
            for name in ("key", "fill", "ambient", "rim", "specular")
        )
        + "    },\n"
        for look, style in looks.items()
    )
    return (
        "// Generated from printstash-core/mesh/preview_profile.json. "
        "Do not edit by hand.\n"
        "export const THUMBNAIL_PROFILE = {\n"
        f"  version: {profile['version']},\n"
        f"  aspectRatio: [{aspect[0]}, {aspect[1]}],\n"
        f"  background: {json.dumps(profile['background'])},\n"
        f"  upAxis: {json.dumps(profile['upAxis'])},\n"
        "  hero: { "
        f"azimuthDegrees: {hero['azimuthDegrees']}, "
        f"elevationDegrees: {hero['elevationDegrees']} }},\n"
        "  flat: { "
        f"tiltDegrees: {flat['tiltDegrees']}, "
        f"thicknessRatio: {flat['thicknessRatio']} }},\n"
        f"  marginFraction: {profile['marginFraction']},\n"
        "  supersampling: { "
        f"maxOutputWidth: {supersampling['maxOutputWidth']}, "
        f"smallFactor: {supersampling['smallFactor']}, "
        f"largeFactor: {supersampling['largeFactor']} }},\n"
        "  encoding: {\n"
        f"    format: {json.dumps(encoding['format'])},\n"
        f"    lossless: {str(encoding['lossless']).lower()},\n"
        f"    quality: {encoding['quality']},\n"
        f"    alphaQuality: {encoding['alphaQuality']},\n"
        f"    exact: {str(encoding['exact']).lower()},\n"
        f"    method: {encoding['method']},\n"
        "  },\n"
        "} as const;\n"
        "export const PREVIEW_LOOK_COLOURS = {\n"
        + "".join(colour_properties)
        + "} as const;\n"
        "/** Studio shading of generated images, in the camera frame (x right, y up, z to viewer). */\n"
        "export const PREVIEW_STUDIO = {\n"
        f"  key: {{ direction: {numbers(lighting['key']['direction'])}, "
        f"colour: {numbers(lighting['key']['colour'])} }},\n"
        f"  fill: {{ direction: {numbers(lighting['fill']['direction'])}, "
        f"colour: {numbers(lighting['fill']['colour'])} }},\n"
        f"  rim: {{ colour: {numbers(lighting['rim']['colour'])}, "
        f"power: {lighting['rim']['power']} }},\n"
        f"  specularPower: {lighting['specular']['power']},\n"
        "  looks: {\n" + studio_looks + "  },\n"
        "} as const;\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_typescript(json.loads(MANIFEST.read_text()))
    if args.check:
        return 0 if OUTPUT.exists() and OUTPUT.read_text() == expected else 1
    OUTPUT.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
