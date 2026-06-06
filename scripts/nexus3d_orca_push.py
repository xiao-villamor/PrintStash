#!/usr/bin/env python3
"""Compatibility wrapper for the renamed PrintStash OrcaSlicer hook.

Use ``scripts/printstash_orca_push.py`` for new installs. This file remains so
existing OrcaSlicer post-processing configurations keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from printstash_orca_push import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
