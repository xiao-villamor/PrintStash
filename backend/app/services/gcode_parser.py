"""Compatibility facade for framework-neutral G-code metadata parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from printstash_core.gcode import parse as parse_metadata
from printstash_core.gcode import parse_duration as parse_duration


def parse(path: Path) -> dict[str, Any]:
    """Return the legacy metadata mapping consumed by application services."""
    return parse_metadata(path).to_legacy_dict()
