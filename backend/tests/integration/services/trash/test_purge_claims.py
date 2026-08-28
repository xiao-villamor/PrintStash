"""The claim a purge takes before it deletes anything, and what it refuses.

A purge is the only irreversible act in the library, and it is not atomic with the
storage delete — the row is claimed, the bytes go, then the row goes. Between the claim
and the delete, somebody can restore the model from another tab, or a second purge can
start on the same row. So the claim is a **conditional UPDATE**: it matches only a row
that is still trashed, still at the `deleted_at` the caller read, and not already claimed.
Losing that race is a conflict, not a retry, because both callers believe they are about
to delete the same bytes.

A restore refuses for the mirror reason: once a purge holds the claim, its bytes may
already be gone, and bringing the row back would produce a model whose files no longer
exist.

And the whole thing is blocked outright while an open `managed_storage_namespace_escape`
finding is on the books — that finding means the vault may be pointed at somebody's
mounted library, and no ownership proof is trustworthy until it is resolved.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Model,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditSeverity,
)
from app.services import trash
from app.services.storage_ownership import UnsafeStorageDeleteError
from tests.factories import (
    build_audit_run,
    build_model,
    build_user,
    detached_model,
)


@pytest.fixture
def trashed(db_session: Session):
    made = {"n": 0}

    def build() -> Model:
        made["n"] += 1
        row = build_model(
            db_session,
            name=f"Doomed {made['n']}",
            slug=f"doomed-{made['n']}",
            hash=f"{made['n']:064d}",
            deleted_at=utcnow(),
        )
        return row

    return build


class TestClaimPurge:
    def test_claims_a_trashed_row(self, db_session: Session, trashed) -> None:
        model = trashed()

        token = trash._claim_purge(db_session, model)

        assert token
        assert model.purge_token == token

    def test_refuses_a_row_that_is_already_claimed(
        self, db_session: Session, trashed
    ) -> None:
        model = trashed()
        trash._claim_purge(db_session, model)
        db_session.commit()

        # Two purges believing they are about to delete the same bytes is the
        # race this exists to lose loudly.
        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash._claim_purge(db_session, model)

    def test_refuses_a_row_that_moved_since_it_was_read(
        self, db_session: Session, trashed
    ) -> None:
        from datetime import timedelta

        model = trashed()
        stale = detached_model(
            id=model.id,
            name=model.name,
            slug=model.slug,
            hash=model.hash,
            deleted_at=model.deleted_at,
        )
        db_session.exec(
            Model.__table__.update()  # type: ignore[attr-defined]
            .where(Model.id == model.id)
            .values(deleted_at=utcnow() + timedelta(seconds=1))
        )
        db_session.commit()

        # Somebody restored and re-trashed it between the read and the claim, so
        # the row this caller looked at is no longer the row on disk.
        with pytest.raises(trash.PurgeConflictError):
            trash._claim_purge(db_session, stale)

    def test_refuses_a_row_that_was_never_persisted(self, db_session: Session) -> None:
        with pytest.raises(trash.PurgeConflictError):
            trash._claim_purge(db_session, detached_model(name="x", slug="x"))


class TestRestoreModel:
    def test_brings_a_trashed_model_back(self, db_session: Session, trashed) -> None:
        model = trashed()

        trash.restore_model(db_session, model)

        assert model.deleted_at is None
        assert model.deleted_by is None

    def test_does_nothing_to_a_live_model(self, db_session: Session) -> None:
        model = build_model(db_session, name="Live", slug="live-restore", hash="a" * 64)
        before = model.updated_at

        trash.restore_model(db_session, model)

        assert model.updated_at == before

    def test_refuses_a_model_a_purge_is_already_holding(
        self, db_session: Session, trashed
    ) -> None:
        model = trashed()
        trash._claim_purge(db_session, model)
        db_session.commit()

        # Its bytes may already be gone; restoring would produce a model whose
        # files no longer exist.
        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.restore_model(db_session, model)


class TestDestructiveMaintenanceGuard:
    def test_allows_a_purge_when_nothing_is_flagged(self, db_session: Session) -> None:
        trash._require_destructive_maintenance_safe(db_session)

    def test_blocks_a_purge_while_a_namespace_escape_is_open(
        self, db_session: Session
    ) -> None:
        run = build_audit_run(db_session, build_user(db_session, "purge-auditor"))
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="managed_storage_namespace_escape",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="storage",
                resource_identifier="vault",
                state=VaultAuditFindingState.OPEN,
            )
        )
        db_session.commit()

        # That finding means the vault may be pointed at somebody's mounted
        # library, so no ownership proof is trustworthy until it is resolved.
        with pytest.raises(UnsafeStorageDeleteError, match="storage_cleanup_blocked"):
            trash._require_destructive_maintenance_safe(db_session)

    def test_allows_a_purge_once_the_finding_is_resolved(
        self, db_session: Session
    ) -> None:
        run = build_audit_run(db_session, build_user(db_session, "purge-auditor"))
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="managed_storage_namespace_escape",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="storage",
                resource_identifier="vault",
                state=VaultAuditFindingState.RESOLVED,
            )
        )
        db_session.commit()

        trash._require_destructive_maintenance_safe(db_session)

    def test_ignores_an_open_finding_of_another_kind(self, db_session: Session) -> None:
        run = build_audit_run(db_session, build_user(db_session, "purge-auditor"))
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="orphan_blob",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="storage",
                resource_identifier="vault",
                state=VaultAuditFindingState.OPEN,
            )
        )
        db_session.commit()

        # Only the namespace-escape finding calls ownership into question; the
        # rest must not stop the trash being emptied.
        trash._require_destructive_maintenance_safe(db_session)


class TestPreflightPrimaryKeys:
    def test_does_nothing_when_there_are_no_keys(self, db_session: Session) -> None:
        trash._preflight_primary_keys(db_session, [])

    def test_refuses_when_the_backend_cannot_confirm_write_access(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.storage_backend import get_backend

        def unreachable(_keys: list[str]) -> None:
            raise OSError("read-only file system")

        monkeypatch.setattr(get_backend(), "verify_destructive_access", unreachable)

        # Checked before the first byte goes: a read-only mount must abort the
        # purge rather than delete half of it and fail.
        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_delete_access_unverified"
        ):
            trash._preflight_primary_keys(db_session, ["some/key.bin"])
