"""Migration progress is durable, credential-free and notification delivery is edge-triggered."""

import json

import pytest
from sqlmodel import select

from app.db.models import AuditLog, NotificationDelivery
from app.db.session import get_session_factory
from app.modules.administration.audit import install_audit_listeners
from app.modules.administration.runtime_config import set_notifications_enabled
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.migration_progress import health, project, transition
from app.runtime.maintenance import end_restore_maintenance, hold_restore_maintenance


class TestTransition:
    def test_duplicate_phase_does_not_enqueue_another_notification(
        self, db_session, make_vault_migration, make_notification_channel
    ):
        run = make_vault_migration(phase_history_json="[]")
        set_notifications_enabled(db_session, True)
        make_notification_channel(events=["vault_migration"])

        transition(db_session, run, "copying")
        db_session.commit()
        transition(db_session, run, "copying")
        db_session.commit()

        deliveries = db_session.exec(select(NotificationDelivery)).all()
        assert len(deliveries) == 1
        assert run.notification_events == 1
        context = json.loads(deliveries[0].context_json)
        assert context["migration_id"] == run.id
        assert context["migration_phase"] == "copying"
        assert context["verified_objects"] == 0
        assert "source_config" not in context
        assert (
            len(
                db_session.exec(
                    select(AuditLog).where(AuditLog.action == "vault.migration.copying")
                ).all()
            )
            == 1
        )

    def test_illegal_activation_records_no_transition(
        self, db_session, make_vault_migration
    ):
        install_audit_listeners()
        run = make_vault_migration()
        # The real listeners audit fixture creation too. Rejecting a transition
        # must preserve that history, not require an empty global audit table.
        audit_query = select(AuditLog).order_by(AuditLog.id)
        before = [row.model_dump() for row in db_session.exec(audit_query).all()]
        assert before

        with pytest.raises(ValueError, match="migration_phase_transition_forbidden"):
            transition(db_session, run, "active")

        assert run.state == "planned"
        assert [row.model_dump() for row in db_session.exec(audit_query).all()] == before


class TestProject:
    def test_unavailable_capacity_probe_is_reported_as_unknown(
        self, db_session, tmp_path, make_vault_migration, monkeypatch
    ):
        run = make_vault_migration()
        manager = CapacityManager(get_session_factory())
        handle = manager.reserve(
            "vault-migration:" + run.id,
            [CapacityResource.for_path(tmp_path, 1, role="vault")],
        )

        def unavailable(_resource):
            raise OSError("mount is unavailable")

        monkeypatch.setattr(CapacityResource, "probe", unavailable)
        try:
            result = project(db_session, run)
        finally:
            handle.release()

        assert result["capacity_resources"] == [
            {"role": "vault", "required_bytes": 1, "available_bytes": None}
        ]


class TestHealth:
    def test_empty_history_reports_idle(self, db_session):
        assert health(db_session) == {
            "ok": True,
            "state": "idle",
            "maintenance": False,
            "retryable": False,
            "last_activity_at": None,
        }

    def test_maintenance_overrides_active_state(self, db_session, make_vault_migration):
        make_vault_migration(state="active")
        hold_restore_maintenance()
        try:
            result = health(db_session)
        finally:
            end_restore_maintenance()

        assert result["state"] == "recovery_required"
        assert result["maintenance"] is True
        assert result["ok"] is False
