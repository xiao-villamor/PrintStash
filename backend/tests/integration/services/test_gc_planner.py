"""GC plans separate discovery from destructive authorization.

Contract: a scan may propose exact expired resources, but it cannot remove
catalog rows or storage bytes. Approval requires an independently stored,
verified backup and finalization waits through the quarantine window.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    Document,
    DocumentKind,
    FileType,
    GcRun,
    GcRunState,
    Model,
    RestoreMarker,
)
from app.services import backup, gc_planner
from app.services.storage_backend import S3StorageBackend, StorageTier
from app.services.storage_identity import independent_evidence, s3_target
from tests.factories import (
    build_collection,
    build_failure_domain_declaration,
    build_file,
    build_model,
    build_user,
    detached_collection,
    detached_file,
)


def _expired_model(session: Session, slug: str) -> Model:
    row = build_model(session, name=slug, slug=slug)
    row.deleted_at = utcnow() - timedelta(days=31)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class _Factory:
    def __init__(self, session: Session) -> None:
        self.session = session

    def scoped_session(self):
        session = self.session

        class _Context:
            def __enter__(self):
                return session

            def __exit__(self, *_args):
                return False

        return _Context()


def _evidence() -> tuple[dict, dict]:
    evidence = independent_evidence(
        gc_planner.get_backend().storage_target,
        s3_target(endpoint="", bucket="backups"),
    )
    assert evidence is not None
    return evidence


def _witness(name: str = "backup") -> gc_planner.BackupWitness:
    return gc_planner.BackupWitness(
        backup_id=name,
        source_ref=f"source-{name}",
        provider_ref="e" * 64,
        archive_sha256="b" * 64,
        verified_at=utcnow(),
        active_identity_evidence=_evidence()[0],
        backup_identity_evidence=_evidence()[1],
    )


def _approved_run(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    slug: str,
) -> tuple[Model, GcRun]:
    admin = build_user(session, f"admin-{slug}", superuser=True)
    candidate = _expired_model(session, slug)
    run = gc_planner.create_plan(session, retention_days=30, requested_by=admin.id)
    monkeypatch.setattr(gc_planner, "find_backup_witness", lambda: _witness(slug))
    gc_planner.approve_plan(session, run.id, run.digest, admin.id)
    return candidate, run


class TestCreateGcPlan:
    def test_negative_retention_disables_gc(self, db_session: Session) -> None:
        with pytest.raises(gc_planner.GcSafetyError, match="gc_retention_disabled"):
            gc_planner.create_plan(db_session, retention_days=-1, requested_by=None)

    def test_preview_is_durable_without_deleting_the_candidate(
        self, db_session: Session
    ) -> None:
        admin = build_user(db_session, "gc-preview-admin", superuser=True)
        candidate = _expired_model(db_session, "gc-preview")

        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )

        assert run.state == GcRunState.PREVIEW
        assert run.resource_count == 1
        assert len(run.digest) == 64
        assert db_session.get(Model, candidate.id) is not None

    def test_automatic_batch_is_capped_to_one_percent_of_the_library(
        self, db_session: Session
    ) -> None:
        admin = build_user(db_session, "gc-limit-admin", superuser=True)
        for index in range(200):
            row = build_model(
                db_session,
                name=f"gc-limit-{index}",
                slug=f"gc-limit-{index}",
            )
            if index < 10:
                row.deleted_at = utcnow() - timedelta(days=31)
                db_session.add(row)
        db_session.commit()

        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )

        assert run.resource_count == 2

    def test_refuses_a_second_active_plan(self, db_session: Session) -> None:
        admin = build_user(db_session, "gc-single-plan-admin", superuser=True)
        _expired_model(db_session, "gc-single-plan")
        gc_planner.create_plan(db_session, retention_days=30, requested_by=admin.id)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_active"):
            gc_planner.create_plan(db_session, retention_days=30, requested_by=admin.id)

    def test_candidate_metrics_count_owned_storage_evidence(
        self, db_session: Session
    ) -> None:
        expired = utcnow() - timedelta(days=31)
        model = build_model(db_session, name="metrics", slug="gc-metrics")
        model.deleted_at = expired
        owned = build_file(
            db_session,
            model,
            filename="owned.stl",
            size_bytes=123,
            sha256="1" * 64,
        )
        external = build_file(
            db_session,
            model,
            filename="external.stl",
            size_bytes=999,
            sha256="2" * 64,
            external=True,
        )
        document = Document(
            name="Manual",
            kind=DocumentKind.MARKDOWN,
            body=(
                "/api/v1/documents/5/images/"
                + "a" * 64
                + ".png /api/v1/documents/99/images/"
                + "b" * 64
                + ".png"
            ),
            filename="manual.md",
            deleted_at=expired,
        )
        collection = build_collection(
            db_session,
            name="Collection",
            slug="gc-collection-metrics",
            readme="",
            deleted_at=expired,
        )
        db_session.add_all([model, owned, external, document])
        db_session.commit()
        db_session.refresh(document)
        document.body = f"/api/v1/documents/{document.id}/images/{'a' * 64}.png"
        db_session.refresh(collection)
        collection.readme = (
            f"/api/v1/collections/{collection.id}/images/{'c' * 64}.webp"
        )
        db_session.add_all([document, collection])
        db_session.commit()

        model_candidate = gc_planner._model_candidate(db_session, model)  # noqa: SLF001
        document_candidate = gc_planner._document_candidate(document)  # noqa: SLF001
        collection_candidate = gc_planner._collection_candidate(collection)  # noqa: SLF001

        assert model_candidate.key_count == 4
        assert model_candidate.size_bytes == 123
        assert gc_planner._file_metrics(external) == (0, 0)  # noqa: SLF001
        assert document_candidate.key_count == 2
        assert collection_candidate.key_count == 1

    def test_batch_limits_stop_before_unsafe_candidates(
        self, db_session: Session
    ) -> None:
        deleted_at = utcnow() - timedelta(days=31)
        safe = gc_planner._Candidate("model", 1, deleted_at, 1, 1)  # noqa: SLF001
        too_many_keys = gc_planner._Candidate(  # noqa: SLF001
            "model",
            2,
            deleted_at,
            gc_planner._MAX_KEYS,
            1,  # noqa: SLF001
        )
        too_many_bytes = gc_planner._Candidate(  # noqa: SLF001
            "model",
            3,
            deleted_at,
            1,
            gc_planner._MAX_BYTES + 1,  # noqa: SLF001
        )

        assert gc_planner._select_bounded(  # noqa: SLF001
            db_session, [safe, too_many_keys]
        ) == [safe]
        assert (
            gc_planner._select_bounded(  # noqa: SLF001
                db_session, [too_many_bytes]
            )
            == []
        )


class TestApproveGcPlan:
    def test_rechecks_a_candidate_restored_during_backup_verification(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = build_user(db_session, "gc-concurrent-restore-admin", superuser=True)
        candidate = _expired_model(db_session, "gc-concurrent-restore")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )

        def verify_witness():
            with Session(db_session.bind) as other:
                restored = other.get(Model, candidate.id)
                assert restored is not None
                restored.deleted_at = None
                other.add(restored)
                other.commit()
            return _witness()

        monkeypatch.setattr(gc_planner, "find_backup_witness", verify_witness)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_candidate_changed"):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

        db_session.expire_all()
        assert db_session.get(Model, candidate.id).deleted_at is None
        assert db_session.get(GcRun, run.id).state is GcRunState.PREVIEW

    @pytest.mark.parametrize("backup_bucket", ["shared", "different-bucket"])
    def test_same_server_cannot_authorize_gc_through_a_different_role(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, backup_bucket: str
    ) -> None:
        for key, value in {
            "s3_bucket": "shared",
            "s3_endpoint_url": "https://shared.example.test",
            "s3_region": "us-east-1",
            "s3_access_key": "active-key",
            "s3_secret_key": "active-secret",
            "s3_root": "vault-prefix",
        }.items():
            monkeypatch.setitem(_overlay, key, value)
        active = S3StorageBackend(check_bucket=False)
        monkeypatch.setattr(
            active, "_capabilities", replace(active.capabilities, verified_delete=True)
        )
        assert active.capabilities.tier is StorageTier.VERIFIED
        monkeypatch.setattr(gc_planner, "get_backend", lambda: active)
        target = backup._BackupS3Target(
            None,
            backup_bucket,
            "signature",
            "e" * 64,
            "https://shared.example.test:443",
        )
        assert target.storage_target is not None
        build_failure_domain_declaration(
            db_session, active.storage_target, failure_domain="active-site"
        )
        if target.storage_target.target_ref != active.storage_target.target_ref:
            build_failure_domain_declaration(
                db_session, target.storage_target, failure_domain="claimed-offsite"
            )
        meta = backup.BackupMeta(
            id="shared",
            created_at=utcnow().isoformat(),
            size_bytes=10,
            storage_backend="s3",
            file_count=1,
            app_version="0.13.0",
            path="backup-prefix/shared.tar.gz",
            location="s3",
            archive_sha256="b" * 64,
            provider_ref=target.provider_ref,
            source_ref="backup-profile-source",
            namespace="backup-prefix",
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [meta])
        monkeypatch.setattr(
            backup,
            "verify_backup",
            lambda *_args, **_kwargs: pytest.fail(
                "shared storage must be rejected before archive verification"
            ),
        )
        admin = build_user(db_session, "shared-gc-admin", superuser=True)
        candidate = _expired_model(db_session, "shared-candidate")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        assert run.active_provider_ref != target.provider_ref

        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_required"):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

        assert db_session.get(Model, candidate.id) is not None
        assert run.state is GcRunState.PREVIEW

    @pytest.mark.parametrize("configured", [False, True])
    def test_unknown_target_identity_cannot_authorize_gc(
        self, monkeypatch: pytest.MonkeyPatch, configured: bool
    ) -> None:
        candidate = backup.BackupMeta(
            id="unknown-target",
            created_at=utcnow().isoformat(),
            path="unknown.tar.gz",
            location="s3",
            provider_ref="2" * 64,
            size_bytes=1,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            archive_sha256="a" * 64,
            source_ref="source-ref",
            namespace="backups",
        )
        monkeypatch.setattr(gc_planner, "_active_provider_ref", lambda: "1" * 64)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [candidate])
        target = (
            backup._BackupS3Target(None, "backups", "signature", "2" * 64)
            if configured
            else None
        )
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(
            backup,
            "verify_backup",
            lambda *_args, **_kwargs: backup.BackupVerification(
                backup_id=candidate.id,
                valid=True,
                app_compatible=True,
                manifest_version="3",
                checked_members=1,
                findings=[],
            ),
        )

        assert gc_planner.find_backup_witness() is None

    def test_created_at_parser_normalizes_only_valid_time(self) -> None:
        assert gc_planner._parse_created_at("not-a-date") is None  # noqa: SLF001
        parsed = gc_planner._parse_created_at("2026-01-02T03:04:05")  # noqa: SLF001
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_backup_witness_requires_recent_verified_independent_s3_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        active_ref = "1" * 64
        valid_ref = "2" * 64
        base = {
            "size_bytes": 1,
            "storage_backend": "local",
            "file_count": 1,
            "app_version": "0.13.0",
            "archive_sha256": "a" * 64,
            "source_ref": "source-ref",
            "namespace": "backups",
        }
        candidates = [
            backup.BackupMeta(
                id="local",
                created_at=utcnow().isoformat(),
                path="local.tar.gz",
                location="local",
                provider_ref=valid_ref,
                **base,
            ),
            backup.BackupMeta(
                id="same-provider",
                created_at=utcnow().isoformat(),
                path="same.tar.gz",
                location="s3",
                provider_ref=active_ref,
                **base,
            ),
            backup.BackupMeta(
                id="valid-independent",
                created_at=utcnow().isoformat(),
                path="valid.tar.gz",
                location="s3",
                provider_ref=valid_ref,
                **base,
            ),
        ]
        monkeypatch.setattr(gc_planner, "_active_provider_ref", lambda: active_ref)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: candidates)
        monkeypatch.setattr(
            backup,
            "_get_backup_s3_target",
            lambda: backup._BackupS3Target(None, "backups", "signature", valid_ref, ""),
        )
        monkeypatch.setattr(
            backup,
            "verify_backup",
            lambda backup_id, *, source_ref: backup.BackupVerification(
                backup_id=backup_id,
                valid=True,
                app_compatible=True,
                manifest_version="3",
                checked_members=1,
                findings=[],
            ),
        )

        witness = gc_planner.find_backup_witness()

        assert witness is not None
        assert witness.backup_id == "valid-independent"
        assert witness.provider_ref == valid_ref

    def test_invalid_backup_verification_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = backup.BackupMeta(
            id="invalid",
            created_at=utcnow().isoformat(),
            path="invalid.tar.gz",
            location="s3",
            provider_ref="2" * 64,
            size_bytes=1,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            archive_sha256="a" * 64,
            source_ref="source-ref",
            namespace="backups",
        )
        monkeypatch.setattr(gc_planner, "_active_provider_ref", lambda: "1" * 64)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [candidate])
        monkeypatch.setattr(
            backup,
            "verify_backup",
            lambda *_args, **_kwargs: backup.BackupVerification(
                backup_id="invalid",
                valid=False,
                app_compatible=False,
                manifest_version="3",
                checked_members=0,
                findings=["bad"],
            ),
        )

        assert gc_planner.find_backup_witness() is None

    @pytest.mark.parametrize(
        ("mutation", "error"),
        [
            ("digest", "gc_digest_changed"),
            ("restore", "gc_restore_generation_changed"),
            ("candidate", "gc_candidate_changed"),
        ],
    )
    def test_approval_revalidates_immutable_catalog_evidence(
        self,
        db_session: Session,
        mutation: str,
        error: str,
    ) -> None:
        admin = build_user(db_session, f"gc-revalidate-{mutation}", superuser=True)
        candidate = _expired_model(db_session, f"gc-revalidate-{mutation}")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        if mutation == "digest":
            run.digest = "f" * 64
            db_session.add(run)
            db_session.commit()
        elif mutation == "restore":
            db_session.add(
                RestoreMarker(
                    backup_id="restore-after-preview",
                    operation_nonce="restore-after-preview",
                    archive_sha256="a" * 64,
                    state="completed",
                )
            )
            db_session.commit()
        else:
            candidate.purge_token = "another-cleanup"
            db_session.add(candidate)
            db_session.commit()

        with pytest.raises(gc_planner.GcSafetyError, match=error):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

    def test_approval_requires_an_exact_preview(self, db_session: Session) -> None:
        admin = build_user(db_session, "gc-approval-guards", superuser=True)
        _expired_model(db_session, "gc-approval-guards")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )

        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_found"):
            gc_planner.approve_plan(db_session, 999999, run.digest, admin.id)
        with pytest.raises(gc_planner.GcSafetyError, match="gc_digest_mismatch"):
            gc_planner.approve_plan(db_session, run.id, "0" * 64, admin.id)
        run.state = GcRunState.ABORTED
        run.active_slot = None
        db_session.add(run)
        db_session.commit()
        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_preview"):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

    def test_approval_requires_verified_storage_tier(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = build_user(db_session, "gc-tier-admin", superuser=True)
        _expired_model(db_session, "gc-tier")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        backend = SimpleNamespace(
            capabilities=SimpleNamespace(tier=StorageTier.GUARDED)
        )
        monkeypatch.setattr(gc_planner, "get_backend", lambda: backend)
        monkeypatch.setattr(
            gc_planner, "_active_provider_ref", lambda: run.active_provider_ref
        )

        with pytest.raises(
            gc_planner.GcSafetyError, match="gc_verified_storage_required"
        ):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

    def test_successful_approval_persists_destructive_evidence(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = build_user(db_session, "gc-approved-admin", superuser=True)
        _expired_model(db_session, "gc-approved")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        witness = _witness("approval")
        monkeypatch.setattr(gc_planner, "find_backup_witness", lambda: witness)

        approved = gc_planner.approve_plan(
            db_session, run.id, run.digest, int(admin.id)
        )

        assert approved.state == GcRunState.QUARANTINED
        assert approved.backup_id == witness.backup_id
        assert approved.backup_source_ref == witness.source_ref
        assert approved.backup_provider_ref == witness.provider_ref
        assert approved.backup_archive_sha256 == witness.archive_sha256
        assert approved.quarantine_until is not None
        assert (
            json.loads(approved.active_identity_evidence)
            == witness.active_identity_evidence
        )
        assert (
            json.loads(approved.backup_identity_evidence)
            == witness.backup_identity_evidence
        )

    def test_refuses_approval_without_a_recent_independent_verified_backup(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin = build_user(db_session, "gc-backup-admin", superuser=True)
        _expired_model(db_session, "gc-needs-backup")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        monkeypatch.setattr(gc_planner, "find_backup_witness", lambda: None)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_required"):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

        db_session.refresh(run)
        assert run.state == GcRunState.PREVIEW

    def test_refuses_approval_after_the_storage_provider_changes(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = build_user(db_session, "gc-provider-admin", superuser=True)
        _expired_model(db_session, "gc-provider-drift")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        monkeypatch.setattr(
            gc_planner, "_active_provider_ref", lambda: "provider-that-is-now-active"
        )

        with pytest.raises(gc_planner.GcSafetyError, match="gc_provider_changed"):
            gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)


class TestFinalizeGcPlan:
    def test_rechecks_a_candidate_restored_during_backup_reverification(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate, run = _approved_run(
            db_session, monkeypatch, slug="gc-restore-during-finalize"
        )
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()

        def reverify(_run):
            with Session(db_session.bind) as other:
                restored = other.get(Model, candidate.id)
                assert restored is not None
                restored.deleted_at = None
                other.add(restored)
                other.commit()

        monkeypatch.setattr(gc_planner, "_reverify_backup", reverify)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_candidate_changed"):
            gc_planner.finalize_plan(db_session, run.id)

        db_session.expire_all()
        assert db_session.get(Model, candidate.id).deleted_at is None

    @pytest.mark.parametrize(
        "mutation",
        [
            "domain",
            "revision",
            "withdrawal",
            "legacy-approval",
            "target",
            "archive",
            "compatibility",
            "active-tier",
        ],
    )
    def test_identity_drift_leaves_candidates_restorable(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, mutation: str
    ) -> None:
        admin = build_user(db_session, "gc-domain-admin", superuser=True)
        candidate = _expired_model(db_session, f"gc-domain-{mutation}")
        target = backup._BackupS3Target(
            None, "backups", "signature", "e" * 64, "https://offsite.example.test"
        )
        assert target.storage_target is not None
        declaration = build_failure_domain_declaration(
            db_session, target.storage_target
        )
        meta = backup.BackupMeta(
            id="backup",
            created_at=utcnow().isoformat(),
            size_bytes=10,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            path="backup.tar.gz",
            location="s3",
            archive_sha256="b" * 64,
            provider_ref=target.provider_ref,
            source_ref="source-ref",
            namespace="backups",
        )
        verification = backup.BackupVerification("backup", True, True, "3", 1, [])
        monkeypatch.setattr(backup, "_get_backup_s3_target", lambda: target)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [meta])
        monkeypatch.setattr(
            backup, "verify_backup", lambda *_args, **_kwargs: verification
        )
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        if mutation == "domain":
            declaration.failure_domain = "shared-site"
        elif mutation == "revision":
            declaration.revision = "c" * 32
        elif mutation == "withdrawal":
            db_session.delete(declaration)
        elif mutation == "legacy-approval":
            run.backup_identity_evidence = None
        elif mutation == "target":
            target = replace(target, endpoint="https://elsewhere.example.test")
        elif mutation == "archive":
            meta.archive_sha256 = "c" * 64
        elif mutation == "compatibility":
            verification.app_compatible = False
        else:
            backend = gc_planner.get_backend()
            monkeypatch.setattr(
                backend,
                "_capabilities",
                replace(backend.capabilities, verified_delete=False),
            )
        if mutation in {"domain", "revision"}:
            db_session.add(declaration)
        db_session.add(run)
        db_session.commit()

        with pytest.raises(
            gc_planner.GcSafetyError,
            match="gc_(identity_evidence_changed|backup_witness_invalid|backup_witness_missing|verified_storage_required)",
        ):
            gc_planner.finalize_plan(db_session, run.id)

        db_session.expire_all()
        assert db_session.get(Model, candidate.id) is not None
        assert db_session.get(GcRun, run.id).state == GcRunState.BLOCKED

    def test_only_quarantined_plans_can_finalize(self, db_session: Session) -> None:
        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_found"):
            gc_planner.finalize_plan(db_session, 999999)

        run = gc_planner.create_plan(db_session, retention_days=30, requested_by=None)
        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_quarantined"):
            gc_planner.finalize_plan(db_session, run.id)

    def test_quarantine_deadline_blocks_finalization_without_touching_data(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin = build_user(db_session, "gc-quarantine-admin", superuser=True)
        candidate = _expired_model(db_session, "gc-quarantine")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        witness = gc_planner.BackupWitness(
            backup_id="backup-1",
            source_ref="source-1",
            provider_ref="f" * 64,
            archive_sha256="a" * 64,
            verified_at=utcnow(),
            active_identity_evidence=_evidence()[0],
            backup_identity_evidence=_evidence()[1],
        )
        monkeypatch.setattr(gc_planner, "find_backup_witness", lambda: witness)
        gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_quarantine_active"):
            gc_planner.finalize_plan(db_session, run.id)

        assert db_session.get(Model, candidate.id) is not None

    def test_finalizes_only_the_unchanged_plan_after_quarantine(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        admin = build_user(db_session, "gc-finalize-admin", superuser=True)
        candidate = _expired_model(db_session, "gc-finalize")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        witness = gc_planner.BackupWitness(
            backup_id="backup-finalize",
            source_ref="source-finalize",
            provider_ref="e" * 64,
            archive_sha256="b" * 64,
            verified_at=utcnow(),
            active_identity_evidence=_evidence()[0],
            backup_identity_evidence=_evidence()[1],
        )
        monkeypatch.setattr(gc_planner, "find_backup_witness", lambda: witness)
        gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()
        monkeypatch.setattr(gc_planner, "_reverify_backup", lambda _run: None)

        finalized = gc_planner.finalize_plan(db_session, run.id)

        assert finalized.state == GcRunState.COMPLETED
        assert db_session.get(Model, candidate.id) is None

    @pytest.mark.parametrize(
        ("result", "state", "error"),
        [
            (
                SimpleNamespace(blocked=1, pending=0, completed=0),
                GcRunState.BLOCKED,
                "gc_storage_delete_blocked",
            ),
            (
                SimpleNamespace(blocked=0, pending=1, completed=0),
                GcRunState.FINALIZING,
                "gc_storage_delete_pending",
            ),
        ],
    )
    def test_storage_outbox_result_never_overstates_completion(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        result: SimpleNamespace,
        state: GcRunState,
        error: str,
    ) -> None:
        _candidate, run = _approved_run(
            db_session, monkeypatch, slug=f"gc-outbox-{state.value}"
        )
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()
        monkeypatch.setattr(gc_planner, "_reverify_backup", lambda _run: None)
        monkeypatch.setattr(
            "app.services.storage_deletion.process_storage_delete_intents",
            lambda **_kwargs: result,
        )

        finalized = gc_planner.finalize_plan(db_session, run.id)

        assert finalized.state == state
        assert finalized.last_error == error

    @pytest.mark.parametrize(
        ("resource", "function_name"),
        [
            (
                Document(name="D", kind=DocumentKind.MARKDOWN, deleted_at=utcnow()),
                "hard_delete_document",
            ),
            (
                detached_file(
                    model_id=1,
                    path="owned.stl",
                    original_filename="owned.stl",
                    file_type=FileType.STL,
                    size_bytes=1,
                    sha256="0" * 64,
                    deleted_at=utcnow(),
                ),
                "hard_delete_file",
            ),
            (
                detached_collection(
                    name="C",
                    slug="gc-finalize-collection",
                    path="gc-finalize-collection",
                    deleted_at=utcnow(),
                ),
                "hard_delete_collection",
            ),
        ],
    )
    def test_finalize_dispatches_each_resource_to_its_safe_deleter(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        resource: object,
        function_name: str,
    ) -> None:
        run = GcRun(
            digest="0" * 64,
            retention_days=30,
            cutoff_at=utcnow(),
            state=GcRunState.QUARANTINED,
            quarantine_until=utcnow() - timedelta(seconds=1),
            active_provider_ref="a" * 64,
            restore_generation="b" * 64,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        item = SimpleNamespace(resource_kind="test", resource_id=1)
        called: list[object] = []
        monkeypatch.setattr(gc_planner, "_revalidate_plan", lambda *_args: [item])
        monkeypatch.setattr(gc_planner, "_reverify_backup", lambda _run: None)
        monkeypatch.setattr(gc_planner, "_resource", lambda *_args: resource)
        monkeypatch.setattr(
            f"app.services.trash.{function_name}",
            lambda _session, row: called.append(row),
        )
        monkeypatch.setattr(
            "app.services.storage_deletion.process_storage_delete_intents",
            lambda **_kwargs: SimpleNamespace(blocked=0, pending=0, completed=0),
        )

        finalized = gc_planner.finalize_plan(db_session, run.id)

        assert called == [resource]
        assert finalized.state == GcRunState.COMPLETED

    def test_unexpected_finalization_failure_blocks_with_evidence(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _candidate, run = _approved_run(
            db_session, monkeypatch, slug="gc-finalize-failure"
        )
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()
        monkeypatch.setattr(gc_planner, "_reverify_backup", lambda _run: None)

        def crash(*_args, **_kwargs):
            raise RuntimeError("deleter crashed")

        monkeypatch.setattr("app.services.trash.hard_delete_model", crash)

        with pytest.raises(gc_planner.GcSafetyError, match="gc_finalization_failed"):
            gc_planner.finalize_plan(db_session, run.id)

        db_session.refresh(run)
        assert run.state == GcRunState.BLOCKED
        assert run.active_slot is None
        assert run.last_error == "deleter crashed"


class TestBackupReverification:
    def _run(self) -> GcRun:
        return GcRun(
            digest="0" * 64,
            retention_days=30,
            cutoff_at=utcnow(),
            active_provider_ref="a" * 64,
            restore_generation="b" * 64,
            backup_id="backup",
            backup_source_ref="source",
            backup_provider_ref="e" * 64,
            backup_archive_sha256="f" * 64,
            active_identity_evidence=json.dumps(
                _evidence()[0], sort_keys=True, separators=(",", ":")
            ),
            backup_identity_evidence=json.dumps(
                _evidence()[1], sort_keys=True, separators=(",", ":")
            ),
        )

    def test_missing_or_same_provider_witness_is_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = self._run()
        run.backup_id = None
        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_witness_invalid"):
            gc_planner._reverify_backup(run)  # noqa: SLF001

        run = self._run()
        monkeypatch.setattr(
            gc_planner, "_active_provider_ref", lambda: run.backup_provider_ref
        )
        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_witness_invalid"):
            gc_planner._reverify_backup(run)  # noqa: SLF001

    def test_missing_or_invalid_backup_source_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = self._run()
        monkeypatch.setattr(gc_planner, "_active_provider_ref", lambda: "1" * 64)
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [])
        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_witness_missing"):
            gc_planner._reverify_backup(run)  # noqa: SLF001

        meta = backup.BackupMeta(
            id="backup",
            created_at=utcnow().isoformat(),
            path="backup.tar.gz",
            location="s3",
            provider_ref="e" * 64,
            size_bytes=1,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            archive_sha256="f" * 64,
            source_ref="source",
            namespace="backups",
        )
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [meta])
        monkeypatch.setattr(
            backup,
            "_get_backup_s3_target",
            lambda: backup._BackupS3Target(None, "backups", "signature", "e" * 64, ""),
        )
        monkeypatch.setattr(
            backup,
            "verify_backup",
            lambda *_args, **_kwargs: backup.BackupVerification(
                backup_id="backup",
                valid=False,
                app_compatible=True,
                manifest_version="3",
                checked_members=0,
                findings=["invalid"],
            ),
        )
        with pytest.raises(gc_planner.GcSafetyError, match="gc_backup_witness_invalid"):
            gc_planner._reverify_backup(run)  # noqa: SLF001


class TestAbortGcPlan:
    def test_missing_or_terminal_plan_cannot_be_aborted(
        self, db_session: Session
    ) -> None:
        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_found"):
            gc_planner.abort_plan(db_session, 999999)

        run = gc_planner.create_plan(db_session, retention_days=30, requested_by=None)
        run.state = GcRunState.COMPLETED
        run.active_slot = None
        db_session.add(run)
        db_session.commit()
        with pytest.raises(gc_planner.GcSafetyError, match="gc_plan_not_abortable"):
            gc_planner.abort_plan(db_session, run.id)

    def test_abort_keeps_every_candidate_restorable(self, db_session: Session) -> None:
        admin = build_user(db_session, "gc-abort-admin", superuser=True)
        candidate = _expired_model(db_session, "gc-abort")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )

        aborted = gc_planner.abort_plan(db_session, run.id)

        assert aborted.state == GcRunState.ABORTED
        assert db_session.get(Model, candidate.id) is not None


class TestScheduledGc:
    def test_scheduler_pauses_during_restore_or_disabled_retention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "restore_in_progress", lambda: True)
        assert gc_planner.run_scheduled_gc(retention_days=30)["gc_candidates"] == 0

        monkeypatch.setattr(backup, "restore_in_progress", lambda: False)
        assert gc_planner.run_scheduled_gc(retention_days=-1)["gc_candidates"] == 0

    def test_hourly_run_only_creates_a_preview(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = _expired_model(db_session, "gc-hourly-preview")

        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )

        result = gc_planner.run_scheduled_gc(retention_days=30)

        assert result["rows"] == 0
        assert result["gc_candidates"] == 1
        assert db_session.get(Model, candidate.id) is not None

    def test_hourly_run_retries_a_finalizing_storage_outbox(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = build_user(db_session, "gc-retry-admin", superuser=True)
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        run.state = GcRunState.FINALIZING
        db_session.add(run)
        db_session.commit()

        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )
        monkeypatch.setattr(
            "app.services.storage_deletion.process_storage_delete_intents",
            lambda **_kwargs: SimpleNamespace(blocked=0, pending=0, completed=1),
        )

        result = gc_planner.run_scheduled_gc(retention_days=30)

        db_session.refresh(run)
        assert result["rows"] == run.resource_count
        assert run.state == GcRunState.COMPLETED
        assert run.active_slot is None

    @pytest.mark.parametrize(
        ("outbox", "state", "error"),
        [
            (
                SimpleNamespace(blocked=1, pending=0, completed=0),
                GcRunState.BLOCKED,
                "gc_storage_delete_blocked",
            ),
            (
                SimpleNamespace(blocked=0, pending=1, completed=0),
                GcRunState.FINALIZING,
                "gc_storage_delete_pending",
            ),
        ],
    )
    def test_hourly_retry_preserves_blocked_or_pending_outbox_state(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        outbox: SimpleNamespace,
        state: GcRunState,
        error: str,
    ) -> None:
        run = gc_planner.create_plan(db_session, retention_days=30, requested_by=None)
        run.state = GcRunState.FINALIZING
        db_session.add(run)
        db_session.commit()
        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )
        monkeypatch.setattr(
            "app.services.storage_deletion.process_storage_delete_intents",
            lambda **_kwargs: outbox,
        )

        result = gc_planner.run_scheduled_gc(retention_days=30)

        db_session.refresh(run)
        assert result["rows"] == 0
        assert run.state == state
        assert run.last_error == error

    def test_hourly_empty_preview_completes_without_approval(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )

        result = gc_planner.run_scheduled_gc(retention_days=30)

        run = db_session.get(GcRun, result["gc_plan_id"])
        assert run is not None
        assert run.resource_count == 0
        assert run.state == GcRunState.COMPLETED
        assert run.active_slot is None

    def test_hourly_scheduler_waits_on_preview_without_approving_it(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = _expired_model(db_session, "gc-scheduled-wait")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=None, scheduled=True
        )
        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )

        result = gc_planner.run_scheduled_gc(retention_days=30)

        assert result["gc_plan_id"] == run.id
        assert result["rows"] == 0
        db_session.refresh(run)
        assert run.state == GcRunState.PREVIEW
        assert db_session.get(Model, candidate.id) is not None

    @pytest.mark.parametrize("finalize_fails", [False, True])
    def test_hourly_scheduler_finalizes_only_expired_quarantine(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        finalize_fails: bool,
    ) -> None:
        run = gc_planner.create_plan(db_session, retention_days=30, requested_by=None)
        run.state = GcRunState.QUARANTINED
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        db_session.add(run)
        db_session.commit()
        monkeypatch.setattr(
            gc_planner, "get_session_factory", lambda: _Factory(db_session)
        )

        if finalize_fails:

            def finalize(*_args, **_kwargs):
                raise gc_planner.GcSafetyError("blocked")
        else:

            def finalize(*_args, **_kwargs):
                run.state = GcRunState.COMPLETED
                return run

        monkeypatch.setattr(gc_planner, "finalize_plan", finalize)

        result = gc_planner.run_scheduled_gc(retention_days=30)

        assert result["gc_plan_id"] == run.id
        assert result["rows"] == (0 if finalize_fails else run.resource_count)


class TestOpenDalS3Witness:
    def _source(self, session, monkeypatch):
        from app.services import backup_destination
        from tests.factories import build_owned_storage_object

        target = s3_target(endpoint="https://offsite.example.test", bucket="backups")
        declaration = build_failure_domain_declaration(session, target)
        row = build_owned_storage_object(
            session,
            backend="s3_self_hosted",
            namespace="backups",
            key="backups/archive.tar.gz",
            object_kind="backup",
            provider_ref="e" * 64,
            sha256="b" * 64,
            token="publication-token",
        )
        meta = backup.BackupMeta(
            id="archive",
            created_at=utcnow().isoformat(),
            size_bytes=3,
            storage_backend="local",
            file_count=1,
            app_version="0.13.0",
            path=row.key,
            location="opendal:s3",
            archive_sha256=row.sha256,
            provider_ref=row.provider_ref,
            source_ref=backup._source_ref(
                location="opendal:s3",
                namespace=row.namespace,
                path=row.key,
                provider_ref=row.provider_ref,
            ),
            namespace=row.namespace,
        )
        destination = SimpleNamespace(backend=SimpleNamespace(storage_target=target))
        monkeypatch.setattr(
            backup_destination, "destination_for_ownership", lambda _: destination
        )
        monkeypatch.setattr(backup, "list_backup_sources", lambda: [meta])
        verification = backup.BackupVerification("archive", True, True, "3", 1, [])
        calls = []

        def verify(backup_id, *, source_ref):
            calls.append((backup_id, source_ref))
            return verification

        monkeypatch.setattr(backup, "verify_backup", verify)
        return row, meta, declaration, destination, verification, calls

    def test_independent_opendal_s3_archive_can_witness_gc(
        self, db_session, monkeypatch
    ):
        _, meta, _, _, _, calls = self._source(db_session, monkeypatch)

        witness = gc_planner.find_backup_witness()

        assert witness is not None
        assert witness.source_ref == meta.source_ref
        assert witness.archive_sha256 == meta.archive_sha256
        assert witness.backup_identity_evidence["target"]["container"] == "backups"
        assert calls == [(meta.id, meta.source_ref)]

    @pytest.mark.parametrize(
        "mutation",
        [
            "domain",
            "target",
            "digest",
            "token",
            "destination",
            "uncommitted",
            "missing-row",
            "missing-digest",
            "missing-target",
            "wrong-transport",
            "source",
            "transport",
        ],
    )
    def test_unprovable_opendal_source_never_authorizes_gc(
        self, db_session, monkeypatch, mutation
    ):
        from app.db.models import StorageObjectState
        from app.services import backup_destination

        row, meta, declaration, destination, _, calls = self._source(
            db_session, monkeypatch
        )
        if mutation == "domain":
            db_session.delete(declaration)
        elif mutation == "target":
            destination.backend.storage_target = s3_target(
                endpoint="http://localhost:9000", bucket="backups"
            )
        elif mutation == "digest":
            row.sha256 = "c" * 64
        elif mutation == "token":
            row.token = None
        elif mutation == "missing-row":
            meta.path = "missing.tar.gz"
            meta.source_ref = backup._source_ref(
                location=meta.location,
                namespace=meta.namespace,
                path=meta.path,
                provider_ref=meta.provider_ref,
            )
        elif mutation == "missing-digest":
            row.sha256 = None
        elif mutation == "missing-target":
            destination.backend.storage_target = None
        elif mutation == "wrong-transport":
            destination.backend.storage_target = (
                destination.backend.storage_target.model_copy(
                    update={"transport": "webdav"}
                )
            )
        elif mutation == "destination":
            monkeypatch.setattr(
                backup_destination, "destination_for_ownership", lambda _: None
            )
        elif mutation == "uncommitted":
            row.state = StorageObjectState.PENDING
        elif mutation == "source":
            meta.source_ref = "different-source"
        else:
            meta.location = "opendal:webdav"
        db_session.add(row)
        db_session.commit()

        assert gc_planner.find_backup_witness() is None
        assert calls == []

    @pytest.mark.parametrize(
        "mutation", ["domain", "target", "archive", "invalid", "compatibility"]
    )
    def test_changed_opendal_witness_preserves_quarantined_candidates(
        self, db_session, monkeypatch, mutation
    ):
        _, meta, declaration, destination, verification, _ = self._source(
            db_session, monkeypatch
        )
        admin = build_user(db_session, "opendal-gc-admin", superuser=True)
        candidate = _expired_model(db_session, "opendal-gc-candidate")
        run = gc_planner.create_plan(
            db_session, retention_days=30, requested_by=admin.id
        )
        gc_planner.approve_plan(db_session, run.id, run.digest, admin.id)
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        if mutation == "domain":
            declaration.failure_domain = "changed-domain"
            db_session.add(declaration)
        elif mutation == "target":
            destination.backend.storage_target = s3_target(
                endpoint="https://changed.example.test", bucket="backups"
            )
        elif mutation == "archive":
            meta.archive_sha256 = "c" * 64
        elif mutation == "invalid":
            verification.valid = False
        else:
            verification.app_compatible = False
        db_session.add(run)
        db_session.commit()

        with pytest.raises(gc_planner.GcSafetyError):
            gc_planner.finalize_plan(db_session, run.id)

        db_session.expire_all()
        assert db_session.get(Model, candidate.id) is not None
        assert db_session.get(GcRun, run.id).state == GcRunState.BLOCKED

    @pytest.mark.parametrize("invalid", ["content", "compatibility"])
    def test_failed_opendal_verification_cannot_witness_gc(
        self, db_session, monkeypatch, invalid
    ):
        _, _, _, _, verification, calls = self._source(db_session, monkeypatch)
        if invalid == "content":
            verification.valid = False
        else:
            verification.app_compatible = False
        assert gc_planner.find_backup_witness() is None
        assert len(calls) == 1

    def test_evidence_changed_during_opendal_verification_is_rejected(
        self, db_session, monkeypatch
    ):
        _, _, declaration, _, verification, _ = self._source(db_session, monkeypatch)

        def verify(*_args, **_kwargs):
            declaration.revision = "c" * 32
            db_session.add(declaration)
            db_session.commit()
            return verification

        monkeypatch.setattr(backup, "verify_backup", verify)
        assert gc_planner.find_backup_witness() is None
