"""Shared local-storage fixture for the purge tests.

A purge is the only place PrintStash deletes bytes, and every guard in
`app/services/trash.py` is written against a *real* storage backend — ownership
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
from app.services.storage_backend import get_backend


@pytest.fixture
def storage(tmp_path: Path):
    _overlay["storage_backend"] = "local"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    (tmp_path / "files").mkdir()
    (tmp_path / "thumbs").mkdir()
    yield get_backend()
    for key in ("storage_backend", "data_dir", "thumb_dir"):
        _overlay.pop(key, None)
