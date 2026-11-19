"""Versioned visual contract shared by every thumbnail renderer."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from importlib.resources import files
from typing import Mapping

# Renderer arithmetic participates in derived visual identities independently
# of camera/material settings and immutable encoder asset manifests.
RASTERIZER_RECIPE = "qualified-portable-v8"

Vector = tuple[float, float, float]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"preview_profile_invalid_{name}")
    return value


def _number(values: Mapping[str, object], key: str) -> float:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"preview_profile_invalid_{key}")
    return float(value)


def _integer(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"preview_profile_invalid_{key}")
    return value


def _boolean(values: Mapping[str, object], key: str) -> bool:
    value = values.get(key)
    if type(value) is not bool:
        raise ValueError(f"preview_profile_invalid_{key}")
    return value


def _vector(
    values: Mapping[str, object], key: str, *, low: float, high: float
) -> Vector:
    value = values.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(
            isinstance(component, (float, int))
            and not isinstance(component, bool)
            and low <= component <= high
            for component in value
        )
    ):
        raise ValueError(f"preview_profile_invalid_{key}")
    return (float(value[0]), float(value[1]), float(value[2]))


def _light_number(
    values: Mapping[str, object], key: str, *, positive: bool = False
) -> float:
    value = _number(values, key)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"preview_profile_invalid_{key}")
    return value


@dataclass(frozen=True)
class DirectionalLight:
    direction: Vector
    colour: Vector
    strength: float


@dataclass(frozen=True)
class PreviewLighting:
    key: DirectionalLight
    fill: DirectionalLight
    rim_colour: Vector
    rim_strength: float
    rim_power: float
    ambient: float
    specular_strength: float
    specular_power: float


@dataclass(frozen=True)
class PreviewProfile:
    version: int
    aspect_ratio: tuple[int, int]
    margin_fraction: float
    material_albedo: tuple[float, float, float]
    hero_azimuth_degrees: float
    hero_elevation_degrees: float
    flat_tilt_degrees: float
    flat_thickness_ratio: float
    supersample_max_output_width: int
    supersample_small_factor: int
    supersample_large_factor: int
    encoding_lossless: bool
    encoding_exact: bool
    encoding_method: int
    encoding_quality: int
    recipe_fingerprint: str

    def supersample_for(self, output_width: int) -> int:
        if output_width <= self.supersample_max_output_width:
            return self.supersample_small_factor
        return self.supersample_large_factor


def _read_profile(filename: str) -> tuple[bytes, Mapping[str, object]]:
    payload = files("printstash_core.mesh").joinpath(filename).read_bytes()
    return payload, _mapping(json.loads(payload), "root")


def _parse_profile(payload: bytes, root: Mapping[str, object]) -> PreviewProfile:
    aspect = root.get("aspectRatio")
    if (
        not isinstance(aspect, list)
        or len(aspect) != 2
        or not all(isinstance(value, int) for value in aspect)
    ):
        raise ValueError("preview_profile_invalid_aspect_ratio")
    hero = _mapping(root.get("hero"), "hero")
    flat = _mapping(root.get("flat"), "flat")
    supersampling = _mapping(root.get("supersampling"), "supersampling")
    encoding = _mapping(root.get("encoding"), "encoding")
    material = _mapping(root.get("material"), "material")
    margin = _number(root, "marginFraction")
    if not 0 < margin < 0.5:
        raise ValueError("preview_profile_invalid_margin")
    method = _integer(encoding, "method")
    quality = _integer(encoding, "quality")
    if not 0 <= method <= 6:
        raise ValueError("preview_profile_invalid_method")
    if not 0 <= quality <= 100:
        raise ValueError("preview_profile_invalid_quality")
    return PreviewProfile(
        version=_integer(root, "version"),
        aspect_ratio=(int(aspect[0]), int(aspect[1])),
        margin_fraction=margin,
        material_albedo=_vector(material, "albedo", low=0, high=1),
        hero_azimuth_degrees=_number(hero, "azimuthDegrees"),
        hero_elevation_degrees=_number(hero, "elevationDegrees"),
        flat_tilt_degrees=_number(flat, "tiltDegrees"),
        flat_thickness_ratio=_number(flat, "thicknessRatio"),
        supersample_max_output_width=_integer(supersampling, "maxOutputWidth"),
        supersample_small_factor=_integer(supersampling, "smallFactor"),
        supersample_large_factor=_integer(supersampling, "largeFactor"),
        encoding_lossless=_boolean(encoding, "lossless"),
        encoding_exact=_boolean(encoding, "exact"),
        encoding_method=method,
        encoding_quality=quality,
        recipe_fingerprint=hashlib.sha256(payload).hexdigest()[:16],
    )


def _directional_light(config: Mapping[str, object]) -> DirectionalLight:
    direction = _vector(config, "direction", low=-1, high=1)
    if not any(direction):
        raise ValueError("preview_profile_invalid_direction")
    return DirectionalLight(
        direction=direction,
        colour=_vector(config, "colour", low=0, high=1),
        strength=_light_number(config, "strength"),
    )


def _parse_lighting(root: Mapping[str, object]) -> PreviewLighting:
    lighting = _mapping(root.get("lighting"), "lighting")
    rim = _mapping(lighting.get("rim"), "rim")
    specular = _mapping(lighting.get("specular"), "specular")
    return PreviewLighting(
        key=_directional_light(_mapping(lighting.get("key"), "key")),
        fill=_directional_light(_mapping(lighting.get("fill"), "fill")),
        rim_colour=_vector(rim, "colour", low=0, high=1),
        rim_strength=_light_number(rim, "strength"),
        rim_power=_light_number(rim, "power", positive=True),
        ambient=_light_number(lighting, "ambient"),
        specular_strength=_light_number(specular, "strength"),
        specular_power=_light_number(specular, "power", positive=True),
    )


_studio_bytes, _studio_config = _read_profile("preview_profile.json")
PREVIEW_PROFILE = _parse_profile(_studio_bytes, _studio_config)
STUDIO_LIGHTING = _parse_lighting(_studio_config)

# The encoder profiles and their cache identity stay independent of studio lighting.
REFERENCE_THUMBNAIL_PROFILE = _parse_profile(
    *_read_profile("reference_thumbnail_profile.json")
)
MATTE_LIGHTING = PreviewLighting(
    key=DirectionalLight((-0.5, 0.65, 1.0), (1.0, 0.98, 0.95), 1.05),
    fill=DirectionalLight((0.55, -0.25, 0.55), (0.55, 0.62, 0.78), 0.30),
    rim_colour=(0.85, 0.92, 1.0),
    rim_strength=0.22,
    rim_power=3.0,
    ambient=0.30,
    specular_strength=0.0,
    specular_power=32.0,
)
REFERENCE_LIGHTING = replace(MATTE_LIGHTING, specular_strength=0.22)


def preview_rasterizer_recipe(*, matte: bool) -> str:
    if matte:
        return RASTERIZER_RECIPE
    return f"{RASTERIZER_RECIPE}-{PREVIEW_PROFILE.recipe_fingerprint}"


__all__ = ["PREVIEW_PROFILE", "REFERENCE_THUMBNAIL_PROFILE", "PreviewProfile"]
