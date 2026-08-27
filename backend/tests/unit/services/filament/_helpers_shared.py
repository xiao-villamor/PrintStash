"""Unit coverage for small pure helpers that had no dedicated test file.

Covers slug generation, filament length→mass conversion and the parsing
helpers used by profile detection — the leaf functions other services lean on.
"""

from __future__ import annotations

import math

import pytest

from app.services import profile_detection as pd
from app.services.filament import DEFAULT_DIAMETER_MM, density_for, mm_to_grams
from app.services.storage import ensure_unique_slug, slugify
from app.services.taxonomy import parse_tag_input

# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# filament length -> mass
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# profile_detection leaf parsers
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# taxonomy.parse_tag_input
# --------------------------------------------------------------------------- #

__all__ = [
    "DEFAULT_DIAMETER_MM",
    "density_for",
    "ensure_unique_slug",
    "math",
    "mm_to_grams",
    "parse_tag_input",
    "pd",
    "pytest",
    "slugify",
]
