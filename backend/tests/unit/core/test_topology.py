"""Two API processes on one vault would corrupt it, so the second must not start.

PrintStash assumes a single writer: SQLite, the storage backend and the job
registry are all written without cross-process coordination. A second API process
pointed at the same vault is silent, gradual corruption rather than an error, so
it is refused at startup by a process lock.

The second row is the one that matters more than it looks. Taking the lock must
**never truncate** a pre-existing file — a lock implementation that opens for
writing would destroy the very data it is protecting, in the exact case it exists
to handle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.core.topology import acquire_process_lock, release_process_lock


class TestProcessLock:
    def test_second_api_process_for_same_vault_fails_fast(self, tmp_path: Path) -> None:
        _overlay["db_url"] = f"sqlite:///{tmp_path / 'vault.db'}"
        first = acquire_process_lock()
        try:
            with pytest.raises(RuntimeError, match="single API process"):
                acquire_process_lock()
        finally:
            release_process_lock(first)

    def test_process_lock_never_truncates_preexisting_file(
        self, tmp_path: Path
    ) -> None:
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
