"""Defends filament at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations


def test_mm_to_grams_pla_default():
    from app.services import filament

    # 1000 mm of 1.75 mm PLA ≈ 2.40 g/m * ... ~ 2.98 g
    grams = filament.mm_to_grams(1000.0, "PLA")
    assert grams is not None and 2.5 < grams < 3.3


def test_mm_to_grams_handles_bad_input():
    from app.services import filament

    assert filament.mm_to_grams(None) is None
    assert filament.mm_to_grams(0) is None
    assert filament.mm_to_grams(-5) is None
