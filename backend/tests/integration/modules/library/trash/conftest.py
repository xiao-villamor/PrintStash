"""Shared local-storage fixture for the purge tests.

A purge is the only place PrintStash deletes bytes, and every guard in
`app/modules/library/trash.py` is written against a *real* storage backend — ownership
receipts carry a device, an inode and a ctime, so nothing in this directory can
be proven against a temp directory that the production backend does not own.
Each test therefore gets its own `data_dir`/`thumb_dir` under `tmp_path`, which
also means a test that fails half-way through a purge cannot leave bytes behind
for the next one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.modules.storage.storage_backend.runtime import get_backend
from tests._env import use_local_storage


@pytest.fixture
def storage(tmp_path: Path):
    _overlay["storage_backend"] = "local"
    use_local_storage(tmp_path)
    yield get_backend()
    for key in ("storage_backend", "data_dir", "thumb_dir", "staging_dir"):
        _overlay.pop(key, None)
