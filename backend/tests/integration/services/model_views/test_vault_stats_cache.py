"""``vault_stats`` must not walk the storage tree on every dashboard load.

The row counts are SQL aggregates and cheap. ``backend.usage()`` is not: locally
it walks every file under the data dir, and on S3 it lists the bucket.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import User
from app.services import model_views
from app.services.auth import hash_password


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path: Path):
    model_views._usage_cache.clear()
    _overlay["storage_backend"] = "local"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    (tmp_path / "files").mkdir()
    (tmp_path / "thumbs").mkdir()
    yield
    model_views._usage_cache.clear()
    for key in ("storage_backend", "data_dir", "thumb_dir"):
        _overlay.pop(key, None)


@pytest.fixture
def admin(db_session: Session) -> User:
    user = User(
        username="dash",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class _CountingBackend:
    def __init__(self) -> None:
        self.calls = 0

    def usage(self, prefix: str = "") -> dict:
        self.calls += 1
        return {
            "backend": "local",
            "ok": True,
            "total_size_bytes": 1,
            "object_count": 1,
        }


def test_storage_usage_is_computed_once_per_window(
    db_session: Session, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _CountingBackend()
    monkeypatch.setattr(model_views, "get_backend", lambda: backend)

    model_views.vault_stats(db_session, admin)
    model_views.vault_stats(db_session, admin)
    model_views.vault_stats(db_session, admin)

    assert backend.calls == 1, "storage was walked on every dashboard load"


def test_cache_expires_after_the_ttl(
    db_session: Session, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _CountingBackend()
    monkeypatch.setattr(model_views, "get_backend", lambda: backend)

    clock = {"now": 1000.0}
    monkeypatch.setattr(model_views, "monotonic", lambda: clock["now"])

    model_views.vault_stats(db_session, admin)
    clock["now"] += model_views._USAGE_TTL_S + 1
    model_views.vault_stats(db_session, admin)

    assert backend.calls == 2


def test_failures_are_not_cached(
    db_session: Session, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient S3 error must not pin an error state for the whole window."""
    calls = {"n": 0}

    class _FlakyBackend:
        def usage(self, prefix: str = "") -> dict:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("s3 down")
            return {
                "backend": "s3",
                "ok": True,
                "total_size_bytes": 5,
                "object_count": 2,
            }

    monkeypatch.setattr(model_views, "get_backend", lambda: _FlakyBackend())

    first = model_views.vault_stats(db_session, admin)
    assert first.storage.ok is False

    second = model_views.vault_stats(db_session, admin)
    assert second.storage.ok is True, "an error response was served from the cache"
