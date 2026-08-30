"""Orphaned storage publication sweep integration tests.

These tests defend stale-intent reclamation and the fail-closed evidence checks
that protect bytes from being deleted after an ownership mismatch.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from app.db.models import OwnedStorageObject, StorageObjectState
from app.services.storage_backend import (
    LocalStorageBackend,
    ObjectIdentity,
    StorageCapabilities,
    StorageObjectInfo,
    StorageTier,
    get_backend,
)
from app.services.storage_ownership import sweep_orphaned_publications

FROZEN_NOW = datetime(2026, 1, 3, tzinfo=UTC)
STALE_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


class _TransientReclaimBackend(LocalStorageBackend):
    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        del key, expected_size, expected_etag, expected_sha256, expected_version_id
        raise OSError("storage temporarily unavailable")


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


class _ReclaimProbeBackend(LocalStorageBackend):
    def __init__(self, *, removed: bool) -> None:
        super().__init__()
        self.removed = removed
        self.info = StorageObjectInfo(size=5, etag="etag-1")
        self.reclaim_calls: list[dict[str, object]] = []

    def object_info(self, key: str) -> StorageObjectInfo | None:
        del key
        return self.info

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        self.reclaim_calls.append(
            {
                "key": key,
                "expected_size": expected_size,
                "expected_etag": expected_etag,
                "expected_sha256": expected_sha256,
                "expected_version_id": expected_version_id,
            }
        )
        return self.removed


class _MismatchedBackend(LocalStorageBackend):
    backend_name = "another-backend"


class _GuardedBackend(LocalStorageBackend):
    backend_name = "guarded-remote"

    def __init__(self) -> None:
        super().__init__()
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )

    def object_info(self, key: str) -> StorageObjectInfo | None:
        del key
        return StorageObjectInfo(size=5, etag="etag")


class TestSweepOrphanedPublications:
    def test_leaves_stale_backup_publication_for_backup_reconciler(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(990)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="backup-cloud-cache",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            sha256="a" * 64,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.pending == 1
        assert db_session.get(OwnedStorageObject, row.id) is not None

    def test_ignores_a_fresh_pending_reservation(self, db_session: Session) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(904)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            sha256="a" * 64,
            created_at=FROZEN_NOW,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.examined == 0
        assert db_session.get(OwnedStorageObject, row.id) is not None

    def test_removes_a_stale_reservation_when_the_object_is_absent(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(905)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            sha256="a" * 64,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.cleared == 1
        assert db_session.get(OwnedStorageObject, row.id) is None

    def test_reclaims_a_matching_small_orphan(self, db_session: Session) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(906)
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
            sha256="f5e6d024c05c9cc2746a3e127408b91a8b7a7f2a30da0c259bc54265502ddef4",
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.reclaimed == 1
        assert not path.exists()

    def test_blocks_an_orphan_with_mismatched_evidence(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(907)
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"someone-elses-bytes")
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            sha256="a" * 64,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert path.read_bytes() == b"someone-elses-bytes"

    def test_preserves_a_same_size_replacement_before_hash_reclamation(
        self, db_session: Session
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
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert path.read_bytes() == b"other"

    def test_blocks_a_large_orphan_without_sufficient_proof(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(909)
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 16 * 1024 * 1024 + 1
        with path.open("wb") as handle:
            handle.truncate(size)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=size,
            sha256="a" * 64,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert path.exists()

    def test_retries_a_transient_reclaim_failure(self, db_session: Session) -> None:
        backend = _TransientReclaimBackend()
        key = backend.thumbnail_key(910)
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
            sha256="f5e6d024c05c9cc2746a3e127408b91a8b7a7f2a30da0c259bc54265502ddef4",
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.pending == 1
        assert row.state is StorageObjectState.PENDING
        assert row.last_error == "OSError"
        assert path.exists()

    def test_never_sweeps_committed_ownership(self, db_session: Session) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(908)
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned")
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.COMMITTED,
            token="proof",
            size_bytes=5,
            created_at=STALE_CREATED_AT,
            committed_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.examined == 0
        assert path.read_bytes() == b"owned"

    def test_blocks_a_pending_reservation_for_another_backend(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(925)
        row = OwnedStorageObject(
            backend="local",
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(
            db_session, _MismatchedBackend(), now=FROZEN_NOW
        )

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert row.last_error == "storage_backend_mismatch"

    def test_guarded_backend_never_check_then_deletes_an_orphan(
        self, db_session: Session
    ) -> None:
        backend = _GuardedBackend()
        key = "remote/guarded-orphan"
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace="remote",
            key=key,
            object_kind="artifact",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert backend.capabilities.tier is StorageTier.GUARDED
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert row.last_error == "storage_reclaim_unsupported"
        assert db_session.get(OwnedStorageObject, row.id) is not None

    def test_reclaims_a_stale_versioned_reservation(self, db_session: Session) -> None:
        backend = _ReclaimProbeBackend(removed=True)
        key = backend.thumbnail_key(926)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            token="pending-token",
            size_bytes=5,
            etag="etag-1",
            version_id="version-1",
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.reclaimed == 1
        assert backend.reclaim_calls[0]["expected_version_id"] == "version-1"
        assert db_session.get(OwnedStorageObject, row.id) is None

    def test_blocks_a_stale_reservation_with_a_size_mismatch(
        self, db_session: Session
    ) -> None:
        backend = _ReclaimProbeBackend(removed=True)
        key = backend.thumbnail_key(927)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            size_bytes=4,
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.last_error == "storage_size_mismatch"
        assert backend.reclaim_calls == []

    def test_reclaims_a_stale_reservation_with_matching_etag(
        self, db_session: Session
    ) -> None:
        backend = _ReclaimProbeBackend(removed=True)
        key = backend.thumbnail_key(928)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="artifact",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            etag="etag-1",
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        assert result.reclaimed == 1
        assert backend.reclaim_calls[0]["expected_etag"] == "etag-1"

    def test_blocks_a_stale_reservation_when_reclaim_does_not_remove(
        self, db_session: Session
    ) -> None:
        backend = _ReclaimProbeBackend(removed=False)
        key = backend.thumbnail_key(929)
        row = OwnedStorageObject(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="artifact",
            state=StorageObjectState.PENDING,
            size_bytes=5,
            etag="etag-1",
            created_at=STALE_CREATED_AT,
        )
        db_session.add(row)
        db_session.commit()

        result = sweep_orphaned_publications(db_session, backend, now=FROZEN_NOW)

        db_session.refresh(row)
        assert result.blocked == 1
        assert row.state is StorageObjectState.BLOCKED
        assert row.last_error == "storage_reclaim_mismatch"
