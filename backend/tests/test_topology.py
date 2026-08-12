from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.core.topology import acquire_process_lock, release_process_lock


def test_second_api_process_for_same_vault_fails_fast(tmp_path: Path) -> None:
    _overlay["db_url"] = f"sqlite:///{tmp_path / 'vault.db'}"
    first = acquire_process_lock()
    try:
        with pytest.raises(RuntimeError, match="single API process"):
            acquire_process_lock()
    finally:
        release_process_lock(first)
