"""Compatibility facade for framework-neutral filament helpers."""

from printstash_core.filament import DEFAULT_DIAMETER_MM as DEFAULT_DIAMETER_MM
from printstash_core.filament import density_for as density_for
from printstash_core.filament import mm_to_grams as mm_to_grams

__all__ = ["DEFAULT_DIAMETER_MM", "density_for", "mm_to_grams"]
