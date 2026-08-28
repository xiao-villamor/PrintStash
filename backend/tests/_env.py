"""Environment setup shared across tiers: storage roots, and nothing else yet.

Distinct from `tests/factories/`, which builds database rows. This is for the
things a test configures *around* the app.

Each helper here has two faces on purpose. The **function** is for a test that
configures storage part-way through its own body, which is how ~130 existing
call sites are written; the **fixture** (in `tests/conftest.py`) is what a new
test should take, because it also tears the configuration down. Both call the
same implementation, so there is one answer to "which directories exist".

Teardown matters less than it looks for the function form: the autouse
`_patch_engine` does `_overlay.clear()` before every test, so a leaked key cannot
survive into the next one. The fixture removes its own keys anyway, so a test
that configures storage and then re-reads settings within the same test sees the
end state it expects.
"""

from __future__ import annotations

from pathlib import Path


def use_local_storage(tmp_path: Path) -> Path:
    """Point every storage directory at a throwaway tree and create them.

    Nine copies of this existed as module-local `_configure_storage` helpers, in
    five variants that differed in exactly the way that causes trouble: some
    created `thumbs/` and `staging/_incoming/`, some created neither, so whether
    a test passed depended on which file it happened to live in. This creates all
    three, which is a superset of every variant.
    """
    from app.core.config import _overlay

    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "thumbs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "staging" / "_incoming").mkdir(parents=True, exist_ok=True)
    return tmp_path


def clear_local_storage() -> None:
    """Drop the storage overlay keys `use_local_storage` set."""
    from app.core.config import _overlay

    for key in ("data_dir", "thumb_dir", "staging_dir"):
        _overlay.pop(key, None)
