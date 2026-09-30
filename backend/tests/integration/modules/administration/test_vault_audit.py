"""Unit coverage for app/modules/administration/vault_audit.py's run lifecycle, per-phase
checks, and finding repair dispatch — beyond the happy-path in test_vault_audit.py."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

import app.modules.backups.backup.catalogue as backup_catalogue
import app.modules.backups.backup.contracts as backup_contracts
import app.modules.backups.backup.targets as backup_targets
import app.modules.backups.backup.verification as backup_verification
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    DocumentKind,
    ExternalLibrary,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    JobKind,
    JobState,
    Model,
    MultipartModel,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.modules.administration import vault_audit
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_utils import (
    OwnedBlob,
    StorageOwnershipSnapshot,
    all_owned_blob_keys,
    ownership_snapshot,
)
from tests.factories import (
    build_collection,
    build_job,
    build_model,
    build_owned_storage_object,
    build_user,
    detached_collection,
    detached_file,
)


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = build_user(session, username, superuser=admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_run(
    session: Session, user: User, mode: VaultAuditMode = VaultAuditMode.QUICK
) -> VaultAuditRun:
    run = VaultAuditRun(requested_by=user.id, mode=mode)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _make_model(session: Session, slug: str) -> Model:
    model = build_model(
        session, name=slug, slug=slug, hash=(slug * 8)[:64].ljust(64, "0")
    )
    return model


def _make_file(session: Session, model: Model, **overrides) -> File:
    defaults = dict(
        model_id=model.id,
        path=f"{model.slug}.stl",
        original_filename=f"{model.slug}.stl",
        file_type=FileType.STL,
        size_bytes=16,
        sha256="a" * 64,
    )
    defaults.update(overrides)
    row = detached_file(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# list_runs / latest_run / request_cancel / reconcile_interrupted_runs
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_primary
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_database
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_external
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_background_jobs
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# execute_run — cancellation, exceptions, embedded/unowned discovery, backups
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ignore_finding / repair_finding
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _details — malformed JSON tolerance
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_database — thumbnail existence-check exception and unreadable image
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_external — stat() raising OSError
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_backups — cancellation mid-loop
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# execute_run — cancellation after external check, after database check,
# after backup check, and embedded-image-missing detection
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _reparse_metadata
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ownership_snapshot — id-less rows and id-mismatched embedded image refs
# --------------------------------------------------------------------------- #


def _patch_exec_injecting_unpersisted_row(
    monkeypatch, db_session, entity_type, extra_row
):
    """Wrap ``session.exec`` so a query for ``entity_type`` also yields
    ``extra_row`` (an in-memory instance with ``id=None`` that was never
    flushed to the DB) alongside the real, persisted rows."""
    original_exec = db_session.exec

    class _ResultWrapper:
        def __init__(self, real_result):
            self._real_result = real_result

        def all(self):
            return [*self._real_result.all(), extra_row]

    def patched_exec(statement, *args, **kwargs):
        real_result = original_exec(statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", None)
        if descriptions and descriptions[0].get("type") is entity_type:
            return _ResultWrapper(real_result)
        return real_result

    monkeypatch.setattr(db_session, "exec", patched_exec)


# --------------------------------------------------------------------------- #
# Run lifecycle — moved here from the maintenance router's test file, which is
# the mirror of app/api/v1/maintenance.py, not of this module.
# --------------------------------------------------------------------------- #


class TestCreateRun:
    def test_starts_a_run_for_the_requesting_user(self, db_session: Session) -> None:
        user = _make_user(db_session, "auditor")

        run, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)

        assert created is True
        assert run.requested_by == user.id

    def test_joins_the_active_run_instead_of_starting_a_second(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "second-auditor")
        first, _ = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)

        second, created = vault_audit.create_run(
            db_session, user.id, VaultAuditMode.FULL
        )

        # Two concurrent walks over the same storage would fight over the same rows.
        assert created is False
        assert second.id == first.id


class TestExecuteRun:
    def test_reports_a_file_whose_blob_is_gone(self, db_session: Session) -> None:
        user = _make_user(db_session, "blob-auditor")
        model = _make_model(db_session, "missing-blob")
        _make_file(db_session, model, path="definitely-missing.stl")
        run, _ = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)

        vault_audit.execute_run(run.id)
        db_session.expire_all()

        result = vault_audit.read_run(db_session, db_session.get(VaultAuditRun, run.id))
        assert result.state == VaultAuditRunState.COMPLETED
        assert any(item.code == "owned_blob_missing" for item in result.findings)

    def test_identifies_a_finding_without_leaking_its_path(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "path-auditor")
        model = _make_model(db_session, "leaky")
        _make_file(db_session, model, path="secret/dir/definitely-missing.stl")
        run, _ = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)

        vault_audit.execute_run(run.id)
        db_session.expire_all()

        result = vault_audit.read_run(db_session, db_session.get(VaultAuditRun, run.id))
        assert all("/" not in item.resource_identifier for item in result.findings)

    def test_execute_run_ignores_non_pending_run(self, db_session: Session) -> None:
        user = _make_user(db_session, "exec-owner")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.COMPLETED
        db_session.add(run)
        db_session.commit()

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.COMPLETED

    def test_execute_run_cancelled_before_primary_check_completes(
        self,
        db_session: Session,
    ) -> None:
        user = _make_user(db_session, "exec-owner2")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "cancel-mid")
        _make_file(db_session, model)
        run.cancel_requested = True
        db_session.add(run)
        db_session.commit()

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED

    def test_execute_run_marks_failed_on_unexpected_exception(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner3")
        run = _make_run(db_session, user)

        def boom(_session):
            raise RuntimeError("snapshot exploded")

        monkeypatch.setattr(vault_audit, "ownership_snapshot", boom)

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.FAILED
        assert run.error_code == "audit_failed"

    def test_auto_repair_retains_an_active_completed_result(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.administration import vault_audit_results

        user = _make_user(db_session, "auto-repair-cancel")
        run = _make_run(db_session, user)
        run.active_slot = "audit"
        db_session.add(run)
        db_session.commit()
        observed: dict[str, object] = {}
        monkeypatch.setattr(
            vault_audit,
            "ownership_snapshot",
            lambda _session: StorageOwnershipSnapshot(),
        )
        monkeypatch.setattr(vault_audit, "_check_primary", lambda *_args: True)
        monkeypatch.setattr(vault_audit, "_check_external", lambda *_args: None)
        monkeypatch.setattr(vault_audit, "_check_database", lambda *_args: None)
        monkeypatch.setattr(vault_audit, "_check_background_jobs", lambda *_args: None)

        def cancel_repair(session: Session, active: VaultAuditRun) -> None:
            session.refresh(active)
            observed.update(
                state=active.state,
                phase=active.current_phase,
                active_slot=active.active_slot,
            )
            cancelled = vault_audit.request_cancel(session, active.id)
            observed["cancel_requested"] = cancelled.cancel_requested

        monkeypatch.setattr(vault_audit_results, "repair_safe_findings", cancel_repair)

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert observed == {
            "state": VaultAuditRunState.COMPLETED,
            "phase": "auto_repair",
            "active_slot": "audit",
            "cancel_requested": True,
        }
        assert run.state == VaultAuditRunState.COMPLETED
        assert run.current_phase == "completed"
        assert run.active_slot is None

    def test_execute_run_flags_every_ownership_problem_in_the_snapshot(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner4")
        run = _make_run(db_session, user)

        snapshot = StorageOwnershipSnapshot(
            discovered_keys={
                "vault/collection-images/9/pic.png",
                "vault/stray/orphan.stl",
            },
        )
        monkeypatch.setattr(
            vault_audit, "ownership_snapshot", lambda _session: snapshot
        )
        monkeypatch.setattr(get_backend(), "exists", lambda _key: True)

        vault_audit.execute_run(run.id)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        codes = {f.code for f in findings}
        assert "embedded_image_unreferenced" in codes
        assert "unowned_blob_detected" in codes
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.COMPLETED

    def test_unowned_finding_reports_storage_metadata(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        user = _make_user(db_session, "unowned-metadata")
        run = _make_run(db_session, user)
        orphan = tmp_path / "retained.stl"
        orphan.write_bytes(b"x" * 2048)
        os.utime(orphan, (1767225600, 1767225600))
        snapshot = StorageOwnershipSnapshot(discovered_keys={str(orphan)})
        backend = get_backend()
        monkeypatch.setattr(
            vault_audit, "ownership_snapshot", lambda _session: snapshot
        )
        monkeypatch.setattr(backend, "stat_size", lambda _key: 2048)
        monkeypatch.setattr(backend, "direct_path", lambda _key: orphan)

        vault_audit.execute_run(run.id)

        result = vault_audit.read_run(db_session, db_session.get(VaultAuditRun, run.id))
        finding = next(
            item for item in result.findings if item.code == "unowned_blob_detected"
        )
        assert finding.details == {
            "actual_size": 2048,
            "modified_at": "2026-01-01T00:00:00+00:00",
        }
        db_session.refresh(run)
        assert run.unclaimed_bytes == 2048
        assert run.unclaimed_unknown_size_count == 0

    def test_unowned_finding_survives_unavailable_storage_metadata(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "unowned-unavailable")
        run = _make_run(db_session, user)
        snapshot = StorageOwnershipSnapshot(discovered_keys={"retained.stl"})
        backend = get_backend()
        monkeypatch.setattr(
            vault_audit, "ownership_snapshot", lambda _session: snapshot
        )
        real_stat_size = backend.stat_size
        real_direct_path = backend.direct_path

        def unavailable_size(key: str):
            if key == "retained.stl":
                raise OSError("storage unavailable")
            return real_stat_size(key)

        def unavailable_path(key: str):
            if key == "retained.stl":
                raise OSError("storage unavailable")
            return real_direct_path(key)

        monkeypatch.setattr(backend, "stat_size", unavailable_size)
        monkeypatch.setattr(backend, "direct_path", unavailable_path)

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        result = vault_audit.read_run(db_session, run)
        finding = next(
            item for item in result.findings if item.code == "unowned_blob_detected"
        )
        assert result.state == VaultAuditRunState.COMPLETED
        assert finding.details == {}
        assert run.unclaimed_bytes == 0
        assert run.unclaimed_unknown_size_count == 1

    def test_execute_run_full_mode_runs_backup_check(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        user = _make_user(db_session, "exec-owner5")
        run = _make_run(db_session, user, VaultAuditMode.FULL)

        _row = build_owned_storage_object(
            db_session,
            backend="local",
            namespace="local/backup",
            key="b1.tar.gz",
            object_kind="backup",
            sha256="a" * 64,
        )
        db_session.commit()
        monkeypatch.setattr(
            backup_verification,
            "verify_backup_ownership",
            lambda ownership_id: backup_contracts.BackupOwnershipVerification(
                ownership_id=ownership_id,
                status="corrupt",
                verification=backup_contracts.BackupVerification(
                    backup_id="b1",
                    valid=False,
                    app_compatible=True,
                    manifest_version="1",
                    checked_members=1,
                    findings=[{"code": "unexpected_code", "member": "a/b.stl"}],
                ),
            ),
        )
        monkeypatch.setattr(
            backup_catalogue,
            "list_backup_sources",
            lambda: pytest.fail("discovery API called"),
        )
        monkeypatch.setattr(
            backup_catalogue, "list_backups", lambda: pytest.fail("list API called")
        )

        vault_audit.execute_run(run.id)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        backup_findings = [f for f in findings if f.resource_type == "backup"]
        assert len(backup_findings) == 1
        # Unrecognized issue codes fall back to the generic manifest-invalid code.
        assert backup_findings[0].code == "backup_manifest_invalid"
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.COMPLETED

    def test_execute_run_honours_a_cancel_between_check_phases(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner6")
        run = _make_run(db_session, user)
        run.cancel_requested = True
        db_session.add(run)
        db_session.commit()

        monkeypatch.setattr(
            vault_audit,
            "ownership_snapshot",
            lambda _session: StorageOwnershipSnapshot(),
        )

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED

    def test_execute_run_returns_when_database_check_cancels(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner7")
        run = _make_run(db_session, user)

        monkeypatch.setattr(
            vault_audit,
            "ownership_snapshot",
            lambda _session: StorageOwnershipSnapshot(),
        )

        def fake_check_database(session, run_arg):
            run_arg.state = VaultAuditRunState.CANCELLED
            session.add(run_arg)
            session.commit()

        monkeypatch.setattr(vault_audit, "_check_database", fake_check_database)

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED
        assert run.current_phase != "completed"

    def test_execute_run_flags_missing_embedded_image(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner8")
        run = _make_run(db_session, user)

        blob = OwnedBlob(
            key="vault/collection-images/1/pic.png",
            resource_type="collection_image",
            resource_id=1,
        )
        snapshot = StorageOwnershipSnapshot(embedded=[blob])
        monkeypatch.setattr(
            vault_audit, "ownership_snapshot", lambda _session: snapshot
        )
        monkeypatch.setattr(get_backend(), "exists", lambda _key: False)

        vault_audit.execute_run(run.id)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "embedded_image_missing" for f in findings)
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.COMPLETED

    def test_execute_run_full_mode_returns_when_backup_check_cancels(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "exec-owner9")
        run = _make_run(db_session, user, VaultAuditMode.FULL)

        monkeypatch.setattr(
            vault_audit,
            "ownership_snapshot",
            lambda _session: StorageOwnershipSnapshot(),
        )

        def fake_check_backups(session, run_arg, *, capacity=None):
            del capacity
            run_arg.state = VaultAuditRunState.CANCELLED
            session.add(run_arg)
            session.commit()

        monkeypatch.setattr(vault_audit, "_check_backups", fake_check_backups)

        vault_audit.execute_run(run.id)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED
        assert run.finished_at is None


class TestReadRun:
    def test_counts_the_findings_that_exist_now(self, db_session: Session) -> None:
        user = _make_user(db_session, "count-auditor")
        run = VaultAuditRun(
            requested_by=user.id,
            mode=VaultAuditMode.QUICK,
            # Stale totals from an earlier pass: the read must not trust them.
            critical_count=0,
            warning_count=99,
            info_count=7,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        db_session.add_all(
            [
                VaultAuditFinding(
                    run_id=run.id,
                    code="owned_blob_missing",
                    severity=VaultAuditSeverity.CRITICAL,
                    resource_type="file",
                    resource_identifier="one.stl",
                ),
                VaultAuditFinding(
                    run_id=run.id,
                    code="owned_blob_missing",
                    severity=VaultAuditSeverity.CRITICAL,
                    resource_type="file",
                    resource_identifier="two.stl",
                ),
                VaultAuditFinding(
                    run_id=run.id,
                    code="metadata_missing",
                    severity=VaultAuditSeverity.WARNING,
                    resource_type="file",
                    resource_identifier="three.gcode",
                ),
            ]
        )
        db_session.commit()

        result = vault_audit.read_run(db_session, run)

        assert (result.critical_count, result.warning_count, result.info_count) == (
            2,
            1,
            0,
        )

    def test_counts_the_same_way_without_the_findings(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "summary-auditor")
        run = VaultAuditRun(
            requested_by=user.id, mode=VaultAuditMode.QUICK, warning_count=99
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="metadata_missing",
                severity=VaultAuditSeverity.WARNING,
                resource_type="file",
                resource_identifier="one.gcode",
            )
        )
        db_session.commit()

        summary = vault_audit.read_run(db_session, run, findings=False)

        assert summary.warning_count == 1
        assert summary.findings == []


class TestDetails:
    def test_details_malformed_json_returns_empty_dict(self) -> None:
        finding = VaultAuditFinding(
            run_id=1,
            code="x",
            severity=VaultAuditSeverity.INFO,
            resource_type="file",
            resource_identifier="x",
            details_json="{not valid json",
        )
        assert vault_audit._details(finding) == {}


class TestListRuns:
    def test_list_runs_returns_the_newest_within_the_limit(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "runs-owner")
        older = _make_run(db_session, user)
        older.created_at = utcnow() - timedelta(hours=1)
        db_session.add(older)
        db_session.commit()
        newer = _make_run(db_session, user)

        rows = vault_audit.list_runs(db_session, limit=1)

        assert len(rows) == 1
        assert rows[0].id == newer.id


class TestLatestRun:
    def test_latest_run_returns_most_recent(self, db_session: Session) -> None:
        user = _make_user(db_session, "latest-owner")
        _make_run(db_session, user)
        newest = _make_run(db_session, user)

        result = vault_audit.latest_run(db_session)

        assert result is not None
        assert result.id == newest.id


class TestRequestCancel:
    def test_request_cancel_flags_active_run(self, db_session: Session) -> None:
        user = _make_user(db_session, "cancel-owner")
        run = _make_run(db_session, user)

        result = vault_audit.request_cancel(db_session, run.id)

        assert result is not None
        assert result.cancel_requested is True

    def test_request_cancel_ignores_terminal_run(self, db_session: Session) -> None:
        user = _make_user(db_session, "cancel-owner2")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.COMPLETED
        db_session.add(run)
        db_session.commit()

        result = vault_audit.request_cancel(db_session, run.id)

        assert result is not None
        assert result.cancel_requested is False

    def test_request_cancel_flags_completed_run_during_auto_repair(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "cancel-auto-repair")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.COMPLETED
        run.current_phase = "auto_repair"
        run.active_slot = "audit"
        db_session.add(run)
        db_session.commit()

        result = vault_audit.request_cancel(db_session, run.id)

        assert result is not None
        assert result.cancel_requested is True

    def test_request_cancel_missing_run_returns_none(self, db_session: Session) -> None:
        assert vault_audit.request_cancel(db_session, 999999) is None


class TestFailInterruptedRun:
    def test_fails_a_run_its_lost_execution_left_running(
        self,
        db_session: Session,
    ) -> None:
        user = _make_user(db_session, "reconcile-owner")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.RUNNING
        db_session.add(run)
        db_session.commit()

        vault_audit.fail_interrupted_run(run.id)

        db_session.refresh(run)
        assert (run.state, run.error_code) == (
            VaultAuditRunState.FAILED,
            "audit_interrupted",
        )

    def test_releases_the_audit_slot_of_an_interrupted_run(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "reconcile-slot")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.RUNNING
        run.active_slot = "audit"
        db_session.add(run)
        db_session.commit()

        vault_audit.fail_interrupted_run(run.id)

        db_session.refresh(run)
        assert run.active_slot is None

    def test_finalizes_an_interrupted_auto_repair_claim(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "reconcile-auto-repair")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.COMPLETED
        run.current_phase = "auto_repair"
        run.active_slot = "audit"
        db_session.add(run)
        db_session.commit()

        vault_audit.fail_interrupted_run(run.id)

        db_session.refresh(run)
        assert (run.state, run.current_phase, run.active_slot) == (
            VaultAuditRunState.COMPLETED,
            "completed",
            None,
        )

    def test_leaves_a_settled_run_alone(self, db_session: Session) -> None:
        user = _make_user(db_session, "reconcile-settled")
        run = _make_run(db_session, user)
        run.state = VaultAuditRunState.COMPLETED
        run.active_slot = None
        db_session.add(run)
        db_session.commit()

        assert vault_audit.fail_interrupted_run(run.id) is False


class TestBlobIsLive:
    def test_unknown_owned_resource_remains_auditable(
        self, db_session: Session
    ) -> None:
        blob = OwnedBlob(
            key="future-kind.bin",
            resource_type="future_kind",
            resource_id=1,
        )

        assert vault_audit._blob_is_live(db_session, blob) is True


class TestCancelled:
    def test_restore_maintenance_cancels_an_active_audit(
        self, db_session, make_user, make_audit_run
    ) -> None:
        from app.runtime.maintenance import (
            begin_restore_maintenance,
            end_restore_maintenance,
        )

        run = make_audit_run(make_user(), state=VaultAuditRunState.RUNNING)
        begin_restore_maintenance()
        try:
            assert vault_audit._cancelled(db_session, run) is True
        finally:
            end_restore_maintenance()

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED
        assert run.error_code == "audit_maintenance"


class TestCheckPrimary:
    def test_check_primary_flags_a_blob_that_does_not_match_its_record(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "primary-owner")
        run = _make_run(db_session, user, VaultAuditMode.FULL)
        get_backend().write_bytes(b"actual-bytes", "size-mismatch.stl")
        get_backend().write_bytes(b"hash-mismatch-content", "hash-mismatch.stl")
        blobs = [
            OwnedBlob(
                key="size-mismatch.stl",
                resource_type="file",
                resource_id=1,
                expected_size=999,
            ),
            OwnedBlob(
                key="hash-mismatch.stl",
                resource_type="file",
                resource_id=2,
                expected_sha256="0" * 64,
            ),
        ]

        completed = vault_audit._check_primary(db_session, run, blobs)

        assert completed is True
        codes = {
            finding.code
            for finding in db_session.exec(
                __import__("sqlmodel")
                .select(VaultAuditFinding)
                .where(VaultAuditFinding.run_id == run.id)
            ).all()
        }
        assert "owned_blob_size_mismatch" in codes
        assert "owned_blob_hash_mismatch" in codes

    def test_check_primary_unreadable_blob_becomes_finding(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "primary-owner2")
        run = _make_run(db_session, user)
        get_backend().write_bytes(b"data", "unreadable.stl")

        def boom(_key: str) -> int:
            raise OSError("disk exploded")

        monkeypatch.setattr(get_backend(), "stat_size", boom)

        completed = vault_audit._check_primary(
            db_session,
            run,
            [OwnedBlob(key="unreadable.stl", resource_type="file", resource_id=3)],
        )

        assert completed is True
        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "owned_blob_unreadable" for f in findings)

    def test_check_primary_stops_when_cancelled(self, db_session: Session) -> None:
        user = _make_user(db_session, "primary-owner3")
        run = _make_run(db_session, user)
        run.cancel_requested = True
        db_session.add(run)
        db_session.commit()

        completed = vault_audit._check_primary(
            db_session,
            run,
            [OwnedBlob(key="whatever.stl", resource_type="file", resource_id=4)],
        )

        assert completed is False
        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED

    def test_check_primary_skips_trashed_artifacts(self, db_session: Session) -> None:
        user = _make_user(db_session, "primary-trashed")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "trashed-artifact")
        file_row = _make_file(db_session, model, path="missing-from-trash.stl")
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        completed = vault_audit._check_primary(
            db_session,
            run,
            [
                OwnedBlob(
                    key=file_row.path,
                    resource_type="file",
                    resource_id=file_row.id,
                    display_name=file_row.original_filename,
                )
            ],
        )

        assert completed is True
        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert findings == []


class TestCheckArtifactCache:
    def test_records_corrupt_disposable_entries(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sqlmodel import select

        from app.modules.storage import materializer_runtime

        user = _make_user(db_session, "cache-audit-corrupt")
        run = _make_run(db_session, user, VaultAuditMode.FULL)

        class CorruptCache:
            def inspect_entries(self, *, full: bool):
                assert full is True
                return {"checked": 2, "corrupt": 1}

        monkeypatch.setattr(
            materializer_runtime, "get_materializer", lambda: CorruptCache()
        )

        vault_audit._check_artifact_cache(db_session, run)

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert [(finding.code, finding.severity) for finding in findings] == [
            ("artifact_cache_corrupt", VaultAuditSeverity.WARNING)
        ]
        assert json.loads(findings[0].details_json) == {"checked": 2, "corrupt": 1}

    def test_records_unavailable_disposable_cache(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sqlmodel import select

        from app.modules.storage import materializer_runtime

        user = _make_user(db_session, "cache-audit-unavailable")
        run = _make_run(db_session, user)

        class UnavailableCache:
            def inspect_entries(self, *, full: bool):
                assert full is False
                raise OSError("cache index unavailable")

        monkeypatch.setattr(
            materializer_runtime, "get_materializer", lambda: UnavailableCache()
        )

        vault_audit._check_artifact_cache(db_session, run)

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert [(finding.code, finding.severity) for finding in findings] == [
            ("artifact_cache_unavailable", VaultAuditSeverity.INFO)
        ]


class TestCheckDatabase:
    def test_check_database_flags_model_without_live_artifact(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "db-owner")
        run = _make_run(db_session, user)
        _make_model(db_session, "no-files")

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "model_without_live_artifact" for f in findings)

    def test_check_database_flags_missing_recommended_revision(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "db-owner2")
        run = _make_run(db_session, user)
        missing_rec = _make_model(db_session, "no-rec")
        _make_file(
            db_session,
            missing_rec,
            file_type=FileType.GCODE,
            is_recommended=False,
            path="a.gcode",
        )

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "recommended_revision_missing" for f in findings)

    def test_check_database_flags_metadata_missing(self, db_session: Session) -> None:
        user = _make_user(db_session, "db-owner3")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "no-meta")
        _make_file(db_session, model)

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "metadata_missing" for f in findings)

    def test_check_database_flags_missing_thumbnail(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "db-owner4")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "no-thumb")
        file_row = _make_file(db_session, model)
        model.thumbnail_file_id = file_row.id
        db_session.add(model)
        db_session.commit()

        # `settings.thumb_dir` is a real, shared absolute path across the whole
        # suite (not per-test tmp_path), so don't rely on it happening to be
        # empty — pin `exists()` so this test can't collide with a leftover
        # thumbnail file another test wrote for the same file id.
        monkeypatch.setattr(get_backend(), "exists", lambda _key: False)

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "thumbnail_missing" for f in findings)

    def test_check_database_stops_when_cancelled(self, db_session: Session) -> None:
        user = _make_user(db_session, "db-owner5")
        run = _make_run(db_session, user)
        run.cancel_requested = True
        db_session.add(run)
        db_session.commit()
        _make_model(db_session, "irrelevant")

        vault_audit._check_database(db_session, run)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED

    def test_check_database_thumbnail_exists_check_raises_marks_missing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "db-owner6")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "thumb-exists-boom")
        file_row = _make_file(db_session, model)
        model.thumbnail_file_id = file_row.id
        db_session.add(model)
        db_session.commit()

        def boom(_key: str) -> bool:
            raise RuntimeError("storage backend unavailable")

        monkeypatch.setattr(get_backend(), "exists", boom)

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "thumbnail_missing" for f in findings)

    def test_check_database_flags_unreadable_thumbnail(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "db-owner7")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "thumb-unreadable")
        file_row = _make_file(db_session, model)
        model.thumbnail_file_id = file_row.id
        db_session.add(model)
        db_session.commit()

        # Thumbnail "exists" but is not a valid image — verify() raises.
        monkeypatch.setattr(get_backend(), "exists", lambda _key: True)

        class _BoomImage:
            def __enter__(self):
                raise OSError("truncated image")

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("PIL.Image.open", lambda _path: _BoomImage())

        vault_audit._check_database(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "thumbnail_unreadable" for f in findings)


class TestCheckExternal:
    def test_check_external_flags_unavailable_root(self, db_session: Session) -> None:
        user = _make_user(db_session, "ext-owner")
        run = _make_run(db_session, user)
        library = ExternalLibrary(name="nas", root_path="/nowhere/does-not-exist")
        db_session.add(library)
        db_session.commit()

        vault_audit._check_external(db_session, run, [])

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "external_root_unavailable" for f in findings)

    def test_check_external_flags_missing_linked_file(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "ext-owner2")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "ext-model")
        file_row = _make_file(
            db_session,
            model,
            path="/nowhere/missing.stl",
            is_external=True,
            external_library_id=None,
        )

        vault_audit._check_external(
            db_session,
            run,
            [
                OwnedBlob(
                    key=file_row.path,
                    resource_type="file",
                    resource_id=file_row.id,
                    display_name="missing.stl",
                )
            ],
        )

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        linked = [f for f in findings if f.code == "linked_file_missing"]
        assert len(linked) == 1
        assert linked[0].repair_action is None

    def test_check_external_skips_trashed_linked_file(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "ext-trashed")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "ext-trashed-model")
        file_row = _make_file(
            db_session,
            model,
            path="/nowhere/trashed.stl",
            is_external=True,
            deleted_at=utcnow(),
        )

        vault_audit._check_external(
            db_session,
            run,
            [
                OwnedBlob(
                    key=file_row.path,
                    resource_type="file",
                    resource_id=file_row.id,
                    display_name=file_row.original_filename,
                )
            ],
        )

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert not any(finding.code == "linked_file_missing" for finding in findings)

    def test_check_external_stat_raises_marks_unavailable(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        user = _make_user(db_session, "ext-owner3")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "ext-stat-boom")
        linked = tmp_path / "linked.stl"
        linked.write_text("x")
        file_row = _make_file(
            db_session,
            model,
            path=str(linked),
            is_external=True,
            external_library_id=None,
        )

        from pathlib import Path as _Path

        original_stat = _Path.stat

        def boom_stat(self, *args, **kwargs):
            if self == linked:
                raise OSError("permission denied")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "stat", boom_stat)

        vault_audit._check_external(
            db_session,
            run,
            [
                OwnedBlob(
                    key=str(linked),
                    resource_type="file",
                    resource_id=file_row.id,
                    display_name="linked.stl",
                )
            ],
        )

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert any(f.code == "linked_file_missing" for f in findings)


class TestCheckBackgroundJobs:
    def test_check_background_jobs_flags_everything_left_stuck(
        self,
        db_session: Session,
    ) -> None:
        user = _make_user(db_session, "jobs-owner")
        run = _make_run(db_session, user)
        build_job(
            db_session,
            kind=JobKind.DERIVATIVES_MESH,
            state=JobState.RUNNING,
            updated_at=utcnow() - timedelta(hours=2),
        )
        stuck_import = InboxItem(
            owner_user_id=user.id,
            source_url="https://example.com/x",
            state=InboxItemState.FAILED,
            retryable=True,
        )
        db_session.add(stuck_import)
        db_session.commit()

        vault_audit._check_background_jobs(db_session, run)

        from sqlmodel import select

        findings = db_session.exec(
            select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
        ).all()
        resource_types = {f.resource_type for f in findings}
        assert "background_job" in resource_types
        assert "pending_import" in resource_types


class TestCheckBackups:
    def test_check_backups_audits_every_authoritative_source(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        user = _make_user(db_session, "backup-census-owner")
        run = _make_run(db_session, user, VaultAuditMode.FULL)
        specs = [
            ("same-id-local.tar.gz", "local", "valid"),
            ("same-id-remote.tar.gz", "backup-s3", "valid"),
            ("missing.tar.gz", "local", "missing"),
            ("inaccessible.tar.gz", "backup-s3", "inaccessible"),
            ("identity.tar.gz", "backup-s3", "identity"),
            ("corrupt.tar.gz", "local", "corrupt"),
        ]
        rows = [
            build_owned_storage_object(
                db_session,
                backend=backend_name,
                namespace=f"{backend_name}/backup",
                key=filename,
                object_kind="backup",
                sha256="a" * 64,
                etag='"etag"' if backend_name == "backup-s3" else None,
                version_id="version" if backend_name == "backup-s3" else None,
            )
            for filename, backend_name, _status in specs
        ]
        db_session.commit()
        verified: list[int] = []
        outcomes = {
            row.id: status
            for row, (_filename, _backend, status) in zip(rows, specs, strict=True)
        }

        def verify(ownership_id: int) -> backup_contracts.BackupOwnershipVerification:
            verified.append(ownership_id)
            status = outcomes[ownership_id]
            if status == "corrupt":
                return backup_contracts.BackupOwnershipVerification(
                    ownership_id=ownership_id,
                    status="corrupt",
                    verification=backup_contracts.BackupVerification(
                        backup_id="corrupt",
                        valid=False,
                        app_compatible=False,
                        manifest_version=None,
                        checked_members=1,
                        findings=[
                            {"code": "backup_member_hash_mismatch", "member": "archive"}
                        ],
                    ),
                )
            return backup_contracts.BackupOwnershipVerification(
                ownership_id=ownership_id,
                status=status,
                error=f"{status} error" if status != "valid" else None,
            )

        monkeypatch.setattr(backup_verification, "verify_backup_ownership", verify)
        monkeypatch.setattr(
            backup_catalogue,
            "list_backup_sources",
            lambda: pytest.fail("discovery API called"),
        )
        monkeypatch.setattr(
            backup_catalogue, "list_backups", lambda: pytest.fail("list API called")
        )

        vault_audit._check_backups(db_session, run)

        assert verified == [row.id for row in rows]
        findings = db_session.exec(
            __import__("sqlmodel")
            .select(VaultAuditFinding)
            .where(VaultAuditFinding.run_id == run.id)
        ).all()
        assert len(findings) == 4
        assert {
            backup_targets.source_reference(
                location="local" if row.backend == "local" else "s3",
                namespace=row.namespace,
                path=row.key,
                provider_ref=row.provider_ref,
            )
            for row in rows[2:]
        } == {item.resource_identifier for item in findings}
        assert {item.code for item in findings} == {
            "backup_storage_inaccessible",
            "backup_identity_unavailable",
            "backup_corrupt",
        }

    def test_check_backups_stops_when_cancelled(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "backup-owner")
        run = _make_run(db_session, user, VaultAuditMode.FULL)
        run.cancel_requested = True
        db_session.add(run)
        db_session.commit()

        vault_audit._check_backups(db_session, run)

        db_session.refresh(run)
        assert run.state == VaultAuditRunState.CANCELLED


class TestIgnoreFinding:
    def test_ignore_finding_marks_ignored(self, db_session: Session) -> None:
        user = _make_user(db_session, "ignore-owner")
        run = _make_run(db_session, user)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="metadata_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="file",
            resource_identifier="x",
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        result = vault_audit.ignore_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.IGNORED
        assert result.resolved_by == user.id

    def test_ignore_finding_missing_returns_none(self, db_session: Session) -> None:
        assert vault_audit.ignore_finding(db_session, 999999, 1) is None


class TestRestoreRecommended:
    def test_repair_finding_restore_recommended_revision(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "repair-owner3")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "repair-rec")
        older = _make_file(
            db_session,
            model,
            file_type=FileType.GCODE,
            path="v1.gcode",
            version=1,
            is_recommended=False,
        )
        newer = _make_file(
            db_session,
            model,
            file_type=FileType.GCODE,
            path="v2.gcode",
            version=2,
            is_recommended=False,
        )
        finding = VaultAuditFinding(
            run_id=run.id,
            code="recommended_revision_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="model",
            resource_identifier=model.name,
            repair_action="restore_recommended_revision",
            details_json=json.dumps({"model_id": model.id}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED
        db_session.refresh(newer)
        db_session.refresh(older)
        assert newer.is_recommended is True
        assert older.is_recommended is False

    def test_repair_finding_restore_recommended_revision_no_files_leaves_unresolved(
        self,
        db_session: Session,
    ) -> None:
        user = _make_user(db_session, "repair-owner3b")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "repair-rec-empty")
        finding = VaultAuditFinding(
            run_id=run.id,
            code="recommended_revision_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="model",
            resource_identifier=model.name,
            repair_action="restore_recommended_revision",
            details_json=json.dumps({"model_id": model.id}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state != VaultAuditFindingState.RESOLVED

    def test_restore_recommended_no_files_returns_false(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "restore-empty")
        assert vault_audit._restore_recommended(db_session, model.id) is False


class TestReparseMetadata:
    def test_repair_finding_reparse_metadata(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "repair-owner4")
        run = _make_run(db_session, user)
        model = _make_model(db_session, "repair-meta")
        file_row = _make_file(db_session, model)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="metadata_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="file",
            resource_identifier=file_row.original_filename,
            repair_action="reparse_metadata",
            details_json=json.dumps({"file_id": file_row.id}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        monkeypatch.setattr(vault_audit, "_reparse_metadata", lambda _s, _id: True)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED

    def test_reparse_metadata_rederives_the_metadata_in_the_background(
        self, db_session: Session, work_engine
    ) -> None:
        model = _make_model(db_session, "reparse-ok")
        file_row = _make_file(
            db_session, model, path="reparse-ok.gcode", file_type=FileType.GCODE
        )
        get_backend().write_bytes(b"; filament_type = PLA\nG28\n", file_row.path)

        vault_audit._reparse_metadata(db_session, file_row.id)
        work_engine.drain()

        from sqlmodel import select

        from app.db.models import Metadata

        db_session.expire_all()
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == file_row.id)
        ).first()
        assert meta is not None and meta.material_type == "PLA"

    def test_reparse_metadata_reads_an_external_source_without_the_vault(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "external.gcode"
        payload = b"G28\n"
        source.write_bytes(payload)
        model = _make_model(db_session, "reparse-external")
        file_row = _make_file(
            db_session,
            model,
            path=str(source),
            file_type=FileType.GCODE,
            is_external=True,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        monkeypatch.setattr(
            vault_audit,
            "get_backend",
            lambda: (_ for _ in ()).throw(AssertionError("vault backend used")),
        )

        result = vault_audit._reparse_metadata(db_session, file_row.id)

        assert result is True

    def test_reparse_metadata_missing_blob_returns_false(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "reparse-missing-blob")
        file_row = _make_file(
            db_session,
            model,
            path="does-not-exist-in-backend.gcode",
            file_type=FileType.GCODE,
        )

        result = vault_audit._reparse_metadata(db_session, file_row.id)

        assert result is False

    def test_reparse_metadata_missing_file_row_returns_false(
        self, db_session: Session
    ) -> None:
        assert vault_audit._reparse_metadata(db_session, 999999) is False

    def test_reparse_metadata_already_has_metadata_is_a_noop_success(
        self,
        db_session: Session,
    ) -> None:
        from app.db.models import Metadata

        model = _make_model(db_session, "reparse-has-meta")
        file_row = _make_file(
            db_session, model, path="reparse-has-meta.gcode", file_type=FileType.GCODE
        )
        db_session.add(Metadata(file_id=file_row.id, material_type="PETG"))
        db_session.commit()

        result = vault_audit._reparse_metadata(db_session, file_row.id)

        assert result is True


class TestRepairFinding:
    def test_disabled_metadata_repair_preserves_an_open_finding(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        make_audit_run,
        make_audit_finding,
        make_system_config,
        make_metadata,
    ):
        from app.core.errors import OperationError

        user = make_user()
        artifact = make_file(make_model(), filename="disabled.stl")
        make_metadata(artifact)
        make_system_config(derivatives_mesh_enabled=False)
        run = make_audit_run(user)
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
            details_json=json.dumps({"file_id": artifact.id}),
        )
        with pytest.raises(OperationError, match="derivative_group_disabled"):
            vault_audit.repair_finding(db_session, finding.id, user.id)
        db_session.refresh(finding)
        assert finding.state is VaultAuditFindingState.OPEN

    def test_repair_finding_already_resolved_is_a_noop(
        self, db_session: Session
    ) -> None:
        user = _make_user(db_session, "repair-owner")
        run = _make_run(db_session, user)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="metadata_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="file",
            resource_identifier="x",
            state=VaultAuditFindingState.RESOLVED,
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED

    def test_repair_finding_missing_returns_none(self, db_session: Session) -> None:
        assert vault_audit.repair_finding(db_session, 999999, 1) is None

    def test_repair_finding_regenerate_thumbnail(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = _make_user(db_session, "repair-owner2")
        run = _make_run(db_session, user)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="thumbnail_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="model",
            resource_identifier="x",
            repair_action="regenerate_thumbnail",
            details_json=json.dumps({"model_id": 1}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        monkeypatch.setattr(vault_audit, "_regenerate_thumbnail", lambda _s, _id: True)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED

    def test_repair_finding_retry_pending_import(self, db_session: Session) -> None:
        user = _make_user(db_session, "repair-owner5")
        run = _make_run(db_session, user)
        item = InboxItem(
            owner_user_id=user.id,
            source_url="https://example.com/x",
            state=InboxItemState.FAILED,
            retryable=True,
            manifest_json="{}",
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="background_job_stuck",
            severity=VaultAuditSeverity.WARNING,
            resource_type="pending_import",
            resource_identifier="x",
            repair_action="retry_pending_import",
            details_json=json.dumps({"inbox_item_id": item.id}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED
        db_session.refresh(item)
        assert item.state == InboxItemState.CAPTURED

    def test_repair_finding_rescan_external_library(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.sources import external_library

        user = _make_user(db_session, "repair-owner6")
        run = _make_run(db_session, user)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="linked_file_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="file",
            resource_identifier="x",
            repair_action="rescan_external_library",
            details_json=json.dumps({"library_id": 1}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        monkeypatch.setattr(
            external_library, "scan_library", lambda _id: {"aborted_unmounted": False}
        )

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state == VaultAuditFindingState.RESOLVED

    def test_repair_finding_rescan_external_library_aborted_leaves_unresolved(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.sources import external_library

        user = _make_user(db_session, "repair-owner7")
        run = _make_run(db_session, user)
        finding = VaultAuditFinding(
            run_id=run.id,
            code="linked_file_missing",
            severity=VaultAuditSeverity.WARNING,
            resource_type="file",
            resource_identifier="x",
            repair_action="rescan_external_library",
            details_json=json.dumps({"library_id": 1}),
        )
        db_session.add(finding)
        db_session.commit()
        db_session.refresh(finding)

        monkeypatch.setattr(
            external_library, "scan_library", lambda _id: {"aborted_unmounted": True}
        )

        result = vault_audit.repair_finding(db_session, finding.id, user.id)

        assert result is not None
        assert result.state != VaultAuditFindingState.RESOLVED


class TestAllOwnedBlobKeys:
    def test_all_owned_blob_keys_covers_external_files_too(
        self,
        db_session: Session,
    ) -> None:
        model = _make_model(db_session, "owned-keys")
        internal = _make_file(db_session, model, path="internal.stl")
        external = _make_file(
            db_session,
            model,
            path="/nas/external.stl",
            is_external=True,
            version=2,
        )

        keys = all_owned_blob_keys(db_session)

        assert internal.path in keys
        assert external.path in keys


class TestOwnershipSnapshot:
    def test_ownership_snapshot_includes_uploaded_multipart_cover(
        self, db_session: Session
    ) -> None:
        multipart = MultipartModel(
            name="Assembly",
            slug="assembly",
            cover_filename="cover.webp",
            cover_content_type="image/webp",
            cover_size_bytes=321,
        )
        db_session.add(multipart)
        db_session.commit()
        db_session.refresh(multipart)

        result = ownership_snapshot(db_session, discover=False)

        matching = [
            blob
            for blob in result.primary
            if blob.resource_type == "multipart_model_cover"
        ]
        assert len(matching) == 1
        assert matching[0].key == get_backend().multipart_model_cover_key(
            multipart.id, "cover.webp"
        )
        assert matching[0].expected_size == 321

    def test_ownership_snapshot_skips_file_row_with_no_id(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _make_model(db_session, "no-id-file")
        _make_file(db_session, model, path="persisted.stl")
        unpersisted = detached_file(
            model_id=model.id,
            path="ghost.stl",
            original_filename="ghost.stl",
        )
        assert unpersisted.id is None
        _patch_exec_injecting_unpersisted_row(
            monkeypatch, db_session, File, unpersisted
        )

        result = ownership_snapshot(db_session, discover=False)

        primary_keys = {blob.key for blob in result.primary}
        derived_keys = {blob.key for blob in result.derived}
        assert "persisted.stl" in primary_keys
        assert "ghost.stl" not in primary_keys
        assert not any(blob.resource_id is None for blob in result.derived)
        assert (
            derived_keys
        )  # the persisted file still contributed thumbnail/stl-cache keys

    def test_ownership_snapshot_skips_document_row_with_no_id(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        persisted = Document(
            name="real-doc", kind=DocumentKind.MARKDOWN, filename="real.md"
        )
        db_session.add(persisted)
        db_session.commit()
        db_session.refresh(persisted)
        unpersisted = Document(
            name="ghost-doc", kind=DocumentKind.MARKDOWN, filename="ghost.md"
        )
        assert unpersisted.id is None
        _patch_exec_injecting_unpersisted_row(
            monkeypatch, db_session, Document, unpersisted
        )

        result = ownership_snapshot(db_session, discover=False)

        primary_names = {
            blob.display_name
            for blob in result.primary
            if blob.resource_type == "document"
        }
        assert "real.md" in primary_names
        assert "ghost.md" not in primary_names

    def test_ownership_snapshot_document_embedded_image_id_must_match_row(
        self,
        db_session: Session,
    ) -> None:
        other = Document(name="other-doc", kind=DocumentKind.MARKDOWN)
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        owner = Document(
            name="owner-doc",
            kind=DocumentKind.MARKDOWN,
            body=f"![pic](/documents/{other.id}/images/stolen.png)",
        )
        db_session.add(owner)
        db_session.commit()
        db_session.refresh(owner)

        result = ownership_snapshot(db_session, discover=False)

        embedded_keys = {blob.key for blob in result.embedded}
        stolen_key = get_backend().document_image_key(other.id, "stolen.png")
        assert stolen_key not in embedded_keys

        owner.body = f"![pic](/documents/{owner.id}/images/mine.png)"
        db_session.add(owner)
        db_session.commit()

        result2 = ownership_snapshot(db_session, discover=False)
        matching = [
            blob
            for blob in result2.embedded
            if blob.resource_type == "document_image" and blob.resource_id == owner.id
        ]
        assert len(matching) == 1
        assert matching[0].key == get_backend().document_image_key(owner.id, "mine.png")
        assert matching[0].display_name == "mine.png"

    def test_ownership_snapshot_skips_collection_row_with_no_id(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_collection(db_session, name="real-col", slug="real-col", path="real-col")
        unpersisted = detached_collection(
            name="ghost-col",
            slug="ghost-col",
            path="ghost-col",
            readme="![pic](/collections/999999/images/never.png)",
        )
        assert unpersisted.id is None
        _patch_exec_injecting_unpersisted_row(
            monkeypatch, db_session, Collection, unpersisted
        )

        result = ownership_snapshot(db_session, discover=False)

        assert not any(
            blob.resource_type == "collection_image" for blob in result.embedded
        )

    def test_ownership_snapshot_collection_embedded_image_id_must_match_row(
        self,
        db_session: Session,
    ) -> None:
        other = build_collection(
            db_session, name="other-col", slug="other-col", path="other-col"
        )

        owner = build_collection(
            db_session,
            name="owner-col",
            slug="owner-col",
            path="owner-col",
            readme=f"![pic](/collections/{other.id}/images/stolen.png)",
        )

        result = ownership_snapshot(db_session, discover=False)

        stolen_key = get_backend().collection_image_key(other.id, "stolen.png")
        assert stolen_key not in {blob.key for blob in result.embedded}

        owner.readme = f"![pic](/collections/{owner.id}/images/mine.png)"
        db_session.add(owner)
        db_session.commit()

        result2 = ownership_snapshot(db_session, discover=False)
        matching = [
            blob
            for blob in result2.embedded
            if blob.resource_type == "collection_image" and blob.resource_id == owner.id
        ]
        assert len(matching) == 1
        assert matching[0].key == get_backend().collection_image_key(
            owner.id, "mine.png"
        )
