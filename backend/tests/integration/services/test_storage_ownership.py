"""Storage publication reserves ownership before bytes become externally visible.

The reservation is durable independently of the caller, while the transition to
committed ownership shares the caller's domain transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import OwnedStorageObject, StorageObjectState
from app.db.session import get_session_factory
from app.services.storage_backend import LocalStorageBackend, get_backend
from app.services.storage_ownership import publish_bytes, sweep_orphaned_publications

FROZEN_NOW = datetime(2026, 1, 3, tzinfo=UTC)
STALE_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


class _FailingLocalStorageBackend(LocalStorageBackend):
    def create_stream(self, src: BytesIO, key: str):
        del src, key
        raise OSError("disk full")


class TestPublishBytes:
    def test_commits_ownership_with_the_callers_transaction(
        self,
        db_session: Session,
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(901)

        publish_bytes(
            db_session,
            backend,
            key,
            b"thumbnail",
            object_kind="thumbnail",
        )
        db_session.commit()

        row = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert row.state is StorageObjectState.COMMITTED

    def test_leaves_pending_intent_when_storage_fails(
        self,
        db_session: Session,
    ) -> None:
        backend = _FailingLocalStorageBackend()
        key = backend.thumbnail_key(902)

        with pytest.raises(OSError, match="disk full"):
            publish_bytes(
                db_session,
                backend,
                key,
                b"thumbnail",
                object_kind="thumbnail",
            )

        with get_session_factory().session() as independent:
            row = independent.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.PENDING
            assert row.last_error == "OSError"

    def test_keeps_pending_intent_when_the_domain_transaction_rolls_back(
        self,
        db_session: Session,
    ) -> None:
        backend = get_backend()
        key = backend.thumbnail_key(903)

        publish_bytes(
            db_session,
            backend,
            key,
            b"thumbnail",
            object_kind="thumbnail",
        )
        db_session.rollback()

        with get_session_factory().session() as independent:
            row = independent.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).one()
            assert row.state is StorageObjectState.PENDING


class TestSweepOrphanedPublications:
    def test_ignores_a_fresh_pending_reservation(
        self,
        db_session: Session,
    ) -> None:
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
        self,
        db_session: Session,
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

    def test_reclaims_a_matching_small_orphan(
        self,
        db_session: Session,
    ) -> None:
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
        self,
        db_session: Session,
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

    def test_never_sweeps_committed_ownership(
        self,
        db_session: Session,
    ) -> None:
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
