"""Orphan reclamation rechecks the exact evidence immediately before deletion."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from app.db.models import OwnedStorageObject, StorageObjectState
from app.services.storage_backend import LocalStorageBackend
from app.services.storage_ownership import sweep_orphaned_publications


class _ReplacementBeforeReclaimBackend(LocalStorageBackend):
    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        Path(key).write_bytes(b"other")
        return super().reclaim_unverified(
            key,
            expected_size=expected_size,
            expected_etag=expected_etag,
            expected_sha256=expected_sha256,
            expected_version_id=expected_version_id,
        )


def test_preserves_a_same_size_replacement_before_hash_reclamation(
    db_session: Session,
) -> None:
    backend = _ReplacementBeforeReclaimBackend()
    key = backend.thumbnail_key(911)
    payload = b"owned"
    path = Path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    row = OwnedStorageObject(
        backend=backend.backend_name,
        namespace=backend.namespace_for(key),
        key=key,
        object_kind="thumbnail",
        state=StorageObjectState.PENDING,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(row)
    db_session.commit()

    result = sweep_orphaned_publications(
        db_session, backend, now=datetime(2026, 1, 3, tzinfo=UTC)
    )

    db_session.refresh(row)
    assert result.blocked == 1
    assert row.state is StorageObjectState.BLOCKED
    assert path.read_bytes() == b"other"
