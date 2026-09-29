"""Defend durable policy, notification, retention, and safe-repair controls."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.errors import OperationError
from app.db.models import AuditLog, FileType, VaultAuditMode
from app.modules.administration.vault_audit_policy import update_policy

NOW = datetime(2026, 9, 6, 2, 30, tzinfo=UTC)


class TestVaultAuditPolicyControls:
    def test_stale_editor_cannot_overwrite_policy(
        self, db_session, make_user, make_audit_policy
    ):
        user = make_user()
        policy = make_audit_policy(user)
        revision = policy.revision
        update_policy(
            db_session,
            VaultAuditMode.QUICK,
            {"expected_revision": revision, "paused": True},
            user.id,
        )
        with pytest.raises(OperationError, match="audit_policy_revision_conflict"):
            update_policy(
                db_session,
                VaultAuditMode.QUICK,
                {"expected_revision": revision, "paused": False},
                user.id,
            )
        db_session.refresh(policy)
        assert policy.paused
        logs = db_session.exec(
            select(AuditLog).where(AuditLog.action == "audit.policy_updated")
        ).all()
        assert len(logs) == 1

    def test_jitter_stays_within_the_safe_window(self, make_user, make_audit_policy):
        from app.modules.administration.vault_audit_policy import slot_jitter

        policy = make_audit_policy(
            make_user(), jitter_seconds=3600, window_minutes=2, next_due_at=NOW
        )
        delay = slot_jitter(policy)
        assert 0 <= delay <= 60
        assert slot_jitter(policy) == delay
        policy.jitter_seconds = 0
        assert slot_jitter(policy) == 0

    def test_overdue_evidence_is_idempotent(
        self,
        db_session,
        make_user,
        make_audit_policy,
        make_system_config,
        make_notification_channel,
    ):
        from app.core.time import ensure_utc
        from app.db.models import NotificationDelivery, VaultAuditEvent
        from app.modules.administration.vault_audit_policy import claim_due, health

        make_system_config(notifications_enabled=True)
        make_notification_channel(events=["storage_audit_overdue"])
        policy = make_audit_policy(
            make_user(), enabled=True, next_due_at=NOW, max_lateness_minutes=10
        )
        for _ in range(2):
            assert (
                claim_due(
                    db_session,
                    now=NOW + timedelta(minutes=11),
                    deferred_reason="maintenance",
                )
                is None
            )
        assert ensure_utc(policy.next_due_at) == NOW
        assert len(db_session.exec(select(VaultAuditEvent)).all()) == 1
        assert len(db_session.exec(select(NotificationDelivery)).all()) == 1
        state = health(db_session, now=NOW + timedelta(minutes=11))["policies"][0]
        assert state["overdue"] and state["enabled"] and state["latest_result"] is None

    def test_launch_retries_back_off_without_dropping_slot(
        self, db_session, make_user, make_audit_policy
    ):
        from app.core.time import ensure_utc
        from app.modules.administration.vault_audit_policy import (
            claim_due,
            defer_launch_failure,
        )

        policy = make_audit_policy(make_user(), enabled=True, next_due_at=NOW)
        defer_launch_failure(db_session, now=NOW)
        assert ensure_utc(policy.retry_after) == NOW + timedelta(seconds=60)
        assert claim_due(db_session, now=NOW + timedelta(seconds=10)) is None
        for _ in range(10):
            defer_launch_failure(db_session, now=NOW)
        assert policy.launch_failures == 6
        assert ensure_utc(policy.retry_after) == NOW + timedelta(seconds=1800)
        assert ensure_utc(policy.next_due_at) == NOW

    @pytest.mark.parametrize(
        "state,event",
        [("failed", "storage_audit_failed"), ("cancelled", "storage_audit_cancelled")],
    )
    def test_terminal_notifications_commit_with_result(
        self,
        db_session,
        make_user,
        make_audit_run,
        make_system_config,
        make_notification_channel,
        state,
        event,
    ):
        from app.db.models import (
            NotificationDelivery,
            VaultAuditEvent,
            VaultAuditRunState,
        )
        from app.modules.administration.vault_audit_results import record_terminal

        make_system_config(notifications_enabled=True)
        make_notification_channel(events=[event])
        run = make_audit_run(
            make_user(),
            trigger="scheduled",
            state=VaultAuditRunState(state),
            finished_at=NOW,
        )
        record_terminal(db_session, run)
        db_session.rollback()
        assert not db_session.exec(select(VaultAuditEvent)).all()
        assert not db_session.exec(select(NotificationDelivery)).all()
        record_terminal(db_session, run)
        db_session.commit()
        record_terminal(db_session, run)
        db_session.commit()
        assert len(db_session.exec(select(VaultAuditEvent)).all()) == 1
        delivery = db_session.exec(select(NotificationDelivery)).one()
        assert delivery.event_type.value == event
        assert (
            '"maintenance_path": "/settings?section=maintenance"'
            in delivery.context_json
        )

    def test_the_cooldown_spaces_deliveries_the_session_already_flushed(
        self,
        db_session,
        monkeypatch,
        make_user,
        make_audit_policy,
        make_audit_run,
        make_audit_finding,
        make_system_config,
        make_notification_channel,
    ):
        # Regression: the cooldown found its deliveries by what was still
        # pending in the session (and by object id), so a flush, or a flushed
        # object's address reused by a new delivery, skipped it silently.
        import json

        from app.core.time import utcnow
        from app.db.models import NotificationDelivery, VaultAuditSeverity
        from app.modules.administration import vault_audit_results
        from app.modules.administration.vault_audit_results import record_success

        enqueue = vault_audit_results.enqueue_storage_event

        def enqueue_then_flush(session, *args, **kwargs):
            created = enqueue(session, *args, **kwargs)
            session.flush()
            return created

        monkeypatch.setattr(
            vault_audit_results, "enqueue_storage_event", enqueue_then_flush
        )
        make_system_config(notifications_enabled=True)
        channel = make_notification_channel(events=["storage_regression"])
        user = make_user()
        make_audit_policy(
            user,
            notification_threshold="critical",
            notification_channels_json=json.dumps([channel.id]),
            notification_cooldown_minutes=60,
        )
        for identifier in ("one", "two"):
            run = make_audit_run(user, finished_at=utcnow())
            make_audit_finding(
                run,
                code="blob_missing",
                resource_identifier=identifier,
                severity=VaultAuditSeverity.CRITICAL,
            )
            record_success(db_session, run)
            db_session.commit()

        rows = db_session.exec(
            select(NotificationDelivery).order_by(NotificationDelivery.id)
        ).all()
        assert len(rows) == 2
        assert rows[1].next_retry_at - rows[0].next_retry_at >= timedelta(minutes=60)

    def test_notification_policy_filters_regressions(
        self,
        db_session,
        make_user,
        make_audit_policy,
        make_audit_run,
        make_audit_finding,
        make_system_config,
        make_notification_channel,
    ):
        import json

        from app.core.time import utcnow
        from app.db.models import NotificationDelivery, VaultAuditSeverity
        from app.modules.administration.vault_audit_results import record_success

        make_system_config(notifications_enabled=True)
        wanted = make_notification_channel(events=["storage_regression"])
        make_notification_channel(events=["storage_regression"])
        user = make_user()
        make_audit_policy(
            user,
            notification_threshold="critical",
            notification_channels_json=json.dumps([wanted.id]),
            notification_cooldown_minutes=60,
        )
        first = make_audit_run(user, finished_at=utcnow())
        make_audit_finding(
            first,
            code="blob_missing",
            resource_identifier="one",
            severity=VaultAuditSeverity.CRITICAL,
        )
        record_success(db_session, first)
        db_session.commit()
        second = make_audit_run(user, finished_at=utcnow())
        make_audit_finding(
            second,
            code="blob_missing",
            resource_identifier="one",
            severity=VaultAuditSeverity.CRITICAL,
        )
        make_audit_finding(
            second,
            code="thumbnail_missing",
            resource_identifier="two",
            severity=VaultAuditSeverity.WARNING,
        )
        record_success(db_session, second)
        db_session.commit()
        rows = db_session.exec(select(NotificationDelivery)).all()
        assert len(rows) == 1 and rows[0].channel_id == wanted.id
        third = make_audit_run(user, finished_at=utcnow())
        make_audit_finding(
            third,
            code="blob_missing",
            resource_identifier="three",
            severity=VaultAuditSeverity.CRITICAL,
        )
        record_success(db_session, third)
        db_session.commit()
        rows = db_session.exec(
            select(NotificationDelivery).order_by(NotificationDelivery.id)
        ).all()
        assert rows[1].next_retry_at - rows[0].next_retry_at >= timedelta(minutes=60)

    def test_retention_keeps_comparison_evidence(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        from app.core.metrics import registry
        from app.db.models import VaultAuditFinding, VaultAuditRun
        from app.modules.administration.vault_audit_observability import (
            prune_details,
            refresh_metrics,
        )
        from app.modules.administration.vault_audit_results import record_success

        user = make_user()
        runs = []
        for index in range(3):
            run = make_audit_run(
                user,
                finished_at=NOW - timedelta(days=120 - index),
                started_at=NOW - timedelta(days=121 - index),
                bytes_read=10,
            )
            make_audit_finding(run, code="thumbnail_missing")
            record_success(db_session, run)
            db_session.commit()
            runs.append(run)
        assert prune_details(db_session, now=NOW) == 1
        assert {
            row.run_id for row in db_session.exec(select(VaultAuditFinding)).all()
        } == {
            runs[1].id,
            runs[2].id,
        }
        assert len(db_session.exec(select(VaultAuditRun)).all()) == 3
        refresh_metrics(db_session)
        assert (
            registry.get_sample_value("printstash_audit_bytes_read", {"mode": "quick"})
            == 30
        )
        assert (
            registry.get_sample_value(
                "printstash_audit_runs", {"trigger": "manual", "result": "completed"}
            )
            == 3
        )

    def test_retention_never_touches_an_active_run(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        from app.db.models import VaultAuditFinding, VaultAuditRunState
        from app.modules.administration.vault_audit_observability import (
            prune_details,
        )

        run = make_audit_run(
            make_user(),
            state=VaultAuditRunState.RUNNING,
            finished_at=NOW - timedelta(days=200),
        )
        make_audit_finding(run, code="thumbnail_missing")

        assert prune_details(db_session, now=NOW) == 0
        assert len(db_session.exec(select(VaultAuditFinding)).all()) == 1

    def test_metrics_export_only_bounded_labels(
        self,
        db_session,
        make_user,
        make_audit_run,
        make_audit_policy,
        make_audit_event,
    ):
        import json

        from app.core.metrics import registry
        from app.db.models import AuditLog
        from app.modules.administration.vault_audit_observability import (
            refresh_metrics,
        )

        user = make_user()
        run = make_audit_run(user)
        make_audit_policy(user, mode="quick", deferred_reason="maintenance")
        # A reason outside the bounded set is reported as ``other``, never as a
        # new label value.
        make_audit_policy(user, mode="full", deferred_reason="novel_reason")
        make_audit_event(run, event_type="storage_regression")
        make_audit_event(run, event_type="not_a_storage_event")
        for verified in (True, False, True):
            db_session.add(
                AuditLog(
                    action="audit.auto_repair",
                    resource_type="vault_audit",
                    diff_json=json.dumps({"verified": verified}),
                )
            )
        db_session.commit()

        refresh_metrics(db_session)

        def sample(name: str, **labels: str) -> float | None:
            return registry.get_sample_value(name, labels)

        assert sample("printstash_audit_deferred", reason="maintenance") == 1
        assert sample("printstash_audit_deferred", reason="other") == 1
        assert sample("printstash_audit_deferred", reason="novel_reason") is None
        assert sample("printstash_audit_notifications", event="storage_regression") == 1
        assert (
            sample("printstash_audit_notifications", event="not_a_storage_event")
            is None
        )
        assert sample("printstash_audit_repairs", result="verified") == 2
        assert sample("printstash_audit_repairs", result="failed") == 1

    def test_failed_repair_is_recorded_safely(
        self,
        db_session,
        local_storage,
        make_user,
        make_model,
        make_file,
        make_audit_run,
        make_audit_finding,
        monkeypatch,
    ):
        import hashlib

        from app.core.time import utcnow
        from app.db.models import VaultAuditEvent, VaultAuditFindingState
        from app.modules.administration.vault_audit_results import repair_safe_findings
        from app.modules.derivatives import producers

        path = local_storage / "safe-derived-source.stl"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = b"source remains intact"
        path.write_bytes(content)
        file = make_file(
            make_model(),
            file_type=FileType.STL,
            path=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        run = make_audit_run(
            make_user(),
            repair_actions_json='["reparse_metadata"]',
            finished_at=utcnow(),
        )
        finding = make_audit_finding(
            run,
            code="metadata_missing",
            repair_action="reparse_metadata",
            details_json=f'{{"file_id":{file.id}}}',
        )

        def broken_renderer(*args):
            raise ValueError("private-path-token-must-not-leak")

        monkeypatch.setattr(producers.mesh_isolation, "generate", broken_renderer)
        repair_safe_findings(db_session, run)
        assert path.read_bytes() == content
        db_session.refresh(finding)
        assert finding.state == VaultAuditFindingState.OPEN
        events = db_session.exec(select(VaultAuditEvent)).all()
        assert [row.event_type for row in events] == ["storage_repair_failed"]
        assert "private" not in events[0].summary_json
        assert db_session.exec(
            select(AuditLog).where(AuditLog.action == "audit.auto_repair")
        ).one()

    def test_ignored_baseline_does_not_hide_worsening(
        self, db_session, make_user, make_audit_run, make_audit_finding
    ):
        import json

        from app.core.time import utcnow
        from app.db.models import VaultAuditFindingState, VaultAuditSeverity
        from app.modules.administration.vault_audit_results import record_success

        user = make_user()
        previous = make_audit_run(user, finished_at=utcnow())
        make_audit_finding(previous, state=VaultAuditFindingState.IGNORED)
        record_success(db_session, previous)
        db_session.commit()
        current = make_audit_run(user, finished_at=utcnow())
        make_audit_finding(current, severity=VaultAuditSeverity.CRITICAL)
        record_success(db_session, current)
        assert json.loads(current.regression_json)["summary"]["worsened"] == 1

    def test_owned_byte_estimate_uses_only_committed_owned_objects(
        self, db_session, make_owned_storage_object
    ):
        from app.db.models import StorageObjectState
        from app.modules.administration.vault_audit_policy import estimated_owned_bytes

        make_owned_storage_object(
            key="audit/artifact", size_bytes=10, object_kind="artifact"
        )
        make_owned_storage_object(
            key="audit/thumbnail", size_bytes=3, object_kind="thumbnail"
        )
        make_owned_storage_object(
            key="audit/pending", size_bytes=100, state=StorageObjectState.PENDING
        )
        assert estimated_owned_bytes(db_session, VaultAuditMode.FULL) == 13
        assert estimated_owned_bytes(db_session, VaultAuditMode.QUICK) == 3
