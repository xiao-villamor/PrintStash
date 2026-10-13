"""Defend immutable audit baselines, safe events, and conservative derived repairs."""

import json
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import (
    NotificationDelivery,
    NotificationEventType,
    VaultAuditEvent,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRunState,
)
from app.modules.administration.vault_audit_results import (
    record_event,
    record_success,
    repair_safe_findings,
)


class TestVaultAuditResults:
    def test_repeated_success_is_idempotent(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        run = make_audit_run(make_user(), finished_at=utcnow())
        make_audit_finding(run)
        record_success(db_session, run)
        db_session.commit()
        record_success(db_session, run)
        db_session.commit()
        assert len(db_session.exec(select(VaultAuditEvent)).all()) == 1

    def test_event_dedup_key_is_idempotent(self, db_session, make_user, make_audit_run):
        run = make_audit_run(make_user(), finished_at=utcnow())

        record_event(
            db_session,
            run,
            NotificationEventType.STORAGE_REGRESSION,
            {"new": 1},
        )
        db_session.commit()
        record_event(
            db_session,
            run,
            NotificationEventType.STORAGE_REGRESSION,
            {"new": 1},
        )
        db_session.commit()

        assert len(db_session.exec(select(VaultAuditEvent)).all()) == 1

    @pytest.mark.parametrize(
        "state", [VaultAuditRunState.FAILED, VaultAuditRunState.CANCELLED]
    )
    def test_preserves_baseline_after_failure(
        self, db_session, make_user, make_audit_run, make_audit_finding, state
    ):
        user = make_user()
        baseline = make_audit_run(user, finished_at=utcnow() - timedelta(hours=1))
        make_audit_finding(baseline)
        record_success(db_session, baseline)
        db_session.commit()
        failed = make_audit_run(user, state=state, finished_at=utcnow())
        record_success(db_session, failed)
        db_session.commit()
        next_run = make_audit_run(user, finished_at=utcnow())
        record_success(db_session, next_run)
        db_session.commit()
        assert next_run.baseline_run_id == baseline.id
        assert json.loads(next_run.regression_json)["summary"]["resolved"] == 1

    @pytest.mark.parametrize(
        "overrides",
        [
            {"mode": VaultAuditMode.FULL},
            {"scope": "other"},
            {"storage_generation": "other"},
        ],
    )
    def test_excludes_incomparable_baseline(
        self, db_session, make_user, make_audit_run, overrides
    ):
        user = make_user()
        baseline = make_audit_run(user, finished_at=utcnow(), **overrides)
        record_success(db_session, baseline)
        db_session.commit()
        run = make_audit_run(user, finished_at=utcnow())
        record_success(db_session, run)
        assert run.baseline_run_id is None

    def test_records_recovery(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        user = make_user()
        baseline = make_audit_run(user, finished_at=utcnow() - timedelta(hours=1))
        make_audit_finding(baseline)
        record_success(db_session, baseline)
        db_session.commit()
        run = make_audit_run(user, finished_at=utcnow())
        record_success(db_session, run)
        db_session.commit()
        events = db_session.exec(
            select(VaultAuditEvent).where(VaultAuditEvent.run_id == run.id)
        ).all()
        assert [row.event_type for row in events] == ["storage_recovery"]

    def test_rejects_unsafe_automatic_repair(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        run = make_audit_run(
            make_user(), repair_actions_json='["restore_recommended_revision"]'
        )
        finding = make_audit_finding(run, repair_action="restore_recommended_revision")
        repair_safe_findings(db_session, run)
        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    def test_cancelled_run_stops_before_repair(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        run = make_audit_run(
            make_user(),
            cancel_requested=True,
            repair_actions_json='["reparse_metadata"]',
        )
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
        )

        repair_safe_findings(db_session, run)

        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    def test_finding_outside_the_enabled_allowlist_stays_open(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        run = make_audit_run(make_user(), repair_actions_json='["reparse_metadata"]')
        finding = make_audit_finding(
            run,
            code="thumbnail_missing",
            repair_action="regenerate_thumbnail",
        )

        repair_safe_findings(db_session, run)

        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    def test_expired_deadline_stops_before_repair(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        run = make_audit_run(
            make_user(),
            deadline_at=utcnow() - timedelta(seconds=1),
            repair_actions_json='["reparse_metadata"]',
        )
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
        )

        repair_safe_findings(db_session, run)

        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    def test_external_source_is_not_automatically_repaired(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        make_audit_run,
        make_audit_finding,
    ):
        file = make_file(make_model(), external=True)
        run = make_audit_run(make_user(), repair_actions_json='["reparse_metadata"]')
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
            details_json=json.dumps({"file_id": file.id}),
        )

        repair_safe_findings(db_session, run)

        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    def test_does_not_change_source_with_invalid_hash(
        self,
        db_session,
        local_storage,
        make_user,
        make_model,
        make_file,
        make_audit_run,
        make_audit_finding,
    ):
        model = make_model()
        path = local_storage / "invalid.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"invalid source")
        file = make_file(model, path=str(path))
        file.sha256 = "0" * 64
        db_session.add(file)
        db_session.commit()
        run = make_audit_run(make_user(), repair_actions_json='["reparse_metadata"]')
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
            details_json=json.dumps({"file_id": file.id}),
        )
        repair_safe_findings(db_session, run)
        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN

    @pytest.mark.parametrize(
        "enabled,events",
        [(False, '["storage_regression"]'), (True, '["print_failed"]')],
    )
    def test_respects_notification_preferences(
        self,
        db_session,
        make_user,
        make_system_config,
        make_notification_channel,
        make_audit_run,
        make_audit_finding,
        enabled,
        events,
    ):
        make_system_config(notifications_enabled=enabled)
        make_notification_channel(events_json=events)
        run = make_audit_run(make_user(), finished_at=utcnow())
        make_audit_finding(run)
        record_success(db_session, run)
        db_session.commit()
        assert db_session.exec(select(NotificationDelivery)).all() == []

    def test_queues_safe_storage_context(
        self,
        db_session,
        make_user,
        make_system_config,
        make_notification_channel,
        make_audit_run,
        make_audit_finding,
    ):
        make_system_config(notifications_enabled=True)
        make_notification_channel(events_json='["storage_regression"]')
        run = make_audit_run(make_user(), finished_at=utcnow())
        make_audit_finding(
            run,
            resource_identifier="private-model-name",
            details_json='{"path":"/private/storage/key"}',
        )
        record_success(db_session, run)
        db_session.commit()
        delivery = db_session.exec(select(NotificationDelivery)).one()
        assert delivery.event_type == NotificationEventType.STORAGE_REGRESSION
        assert "private" not in delivery.context_json
        assert delivery.printer_id is None
        assert delivery.print_job_id is None
        assert json.loads(delivery.context_json)["summary"]["new"] == 1

    def test_result_rollback_cannot_leave_an_alert(
        self,
        db_session,
        make_user,
        make_audit_run,
        make_audit_finding,
        make_system_config,
        make_notification_channel,
    ):
        make_system_config(notifications_enabled=True)
        make_notification_channel(events=["storage_regression"])
        run = make_audit_run(make_user(), finished_at=utcnow())
        make_audit_finding(run)
        record_success(db_session, run)
        db_session.rollback()
        assert db_session.exec(select(VaultAuditEvent)).all() == []
        assert db_session.exec(select(NotificationDelivery)).all() == []
        db_session.refresh(run)
        assert run.result_recorded is False


class TestDisabledRepairs:
    def test_disabled_derivative_repair_keeps_the_audit_finding_open(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        make_audit_run,
        make_audit_finding,
        make_system_config,
    ):
        import hashlib

        from app.modules.storage.storage_backend.runtime import get_backend
        from tests.factories.content import binary_stl

        content = binary_stl()
        key = "disabled-audit.stl"
        get_backend().write_bytes(content, key)
        file = make_file(
            make_model(),
            filename="part.stl",
            path=key,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        make_system_config(derivatives_mesh_enabled=False)
        run = make_audit_run(
            make_user(),
            repair_actions_json='["reparse_metadata"]',
            finished_at=utcnow(),
        )
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
            details_json=json.dumps({"file_id": file.id}),
        )
        repair_safe_findings(db_session, run)
        db_session.refresh(finding)
        assert finding.state is VaultAuditFindingState.OPEN
        assert db_session.exec(select(VaultAuditEvent)).all() == []
