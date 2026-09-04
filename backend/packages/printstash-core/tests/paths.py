"""Anchors for everything a ``printstash-core`` test reads off disk.

See ``backend/tests/paths.py`` for why a test never computes its own depth.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_DIR.parent
SRC_DIR = PACKAGE_ROOT / "src"

FIXTURES_DIR = TESTS_DIR / "fixtures"
