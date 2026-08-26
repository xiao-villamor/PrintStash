"""Stable filesystem anchors shared by backend tests after layout moves."""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
TEST_DATA_DIR = Path(__file__).resolve().parent / "fixtures"
