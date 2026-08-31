"""Versioned visual contract shared by every thumbnail renderer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Mapping


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


@dataclass(frozen=True)
class PreviewProfile:
    version: int
    aspect_ratio: tuple[int, int]
    margin_fraction: float
    hero_azimuth_degrees: float
    hero_elevation_degrees: float
    flat_tilt_degrees: float
    flat_thickness_ratio: float
    supersample_max_output_width: int
    supersample_small_factor: int
    supersample_large_factor: int
    encoding_method: int
    recipe_fingerprint: str

    def supersample_for(self, output_width: int) -> int:
        if output_width <= self.supersample_max_output_width:
            return self.supersample_small_factor
        return self.supersample_large_factor


def _load_profile() -> PreviewProfile:
    payload = files("printstash_core.mesh").joinpath("preview_profile.json").read_bytes()
    raw: object = json.loads(payload)
    root = _mapping(raw, "root")
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
    margin = _number(root, "marginFraction")
    if not 0 < margin < 0.5:
        raise ValueError("preview_profile_invalid_margin")
    return PreviewProfile(
        version=_integer(root, "version"),
        aspect_ratio=(int(aspect[0]), int(aspect[1])),
        margin_fraction=margin,
        hero_azimuth_degrees=_number(hero, "azimuthDegrees"),
        hero_elevation_degrees=_number(hero, "elevationDegrees"),
        flat_tilt_degrees=_number(flat, "tiltDegrees"),
        flat_thickness_ratio=_number(flat, "thicknessRatio"),
        supersample_max_output_width=_integer(supersampling, "maxOutputWidth"),
        supersample_small_factor=_integer(supersampling, "smallFactor"),
        supersample_large_factor=_integer(supersampling, "largeFactor"),
        encoding_method=_integer(encoding, "method"),
        recipe_fingerprint=hashlib.sha256(payload).hexdigest()[:16],
    )


PREVIEW_PROFILE = _load_profile()

__all__ = ["PREVIEW_PROFILE", "PreviewProfile"]
