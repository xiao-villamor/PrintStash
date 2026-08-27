"""Defends ``test_second_api_process_for_same_vault_fails_fast`` behavior for the ``core`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

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


def test_process_lock_never_truncates_preexisting_file(tmp_path: Path) -> None:
    _overlay["db_url"] = f"sqlite:///{tmp_path / 'vault.db'}"
    lock_path = tmp_path / ".printstash-api.lock"
    original = b"pre-existing operator bytes\n"
    lock_path.write_bytes(original)

    handle = acquire_process_lock()
    try:
        assert lock_path.read_bytes() == original
    finally:
        release_process_lock(handle)

    assert lock_path.read_bytes() == original
