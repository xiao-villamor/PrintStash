"""Safe durable phase transitions, progress projections and notifications."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from prometheus_client import Counter, Histogram
from sqlmodel import Session, select

from app.core.metrics import registry
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    AuditLog,
    CapacityReservation,
    NotificationEventType,
    VaultAuditRun,
    VaultMigrationObject,
    VaultMigrationRun,
)
from app.modules.notifications.notifications import enqueue_for_event
from app.modules.storage.capacity import CapacityResource
from app.runtime.maintenance import restore_in_progress

_TRANSITIONS = {
    "planned": {"copying", "discarded", "failed"},
    "copying": {"ready", "paused", "failed", "discarded", "cutover_pending"},
    "paused": {"copying", "discarded", "failed", "cutover_pending"},
    "ready": {"copying", "paused", "cutover_pending", "discarded"},
    "cutover_pending": {"draining", "paused"},
    "draining": {"delta_copy", "paused"},
    "delta_copy": {"verifying", "paused"},
    "verifying": {"activating", "paused"},
    "activating": {"active", "paused"},
    "active": {"cleaned", "complete"},
    "failed": {"copying", "discarded"},
    "recovery_required": {"copying", "active", "paused", "failed"},
    "cleaned": set(),
    "discarded": set(),
    "complete": set(),
}
PHASE_SECONDS = Histogram(
    "printstash_vault_migration_phase_seconds",
    "Completed migration phase duration",
    ["phase", "provider"],
    registry=registry,
)
COPIED_BYTES = Counter(
    "printstash_vault_migration_copied_bytes_total",
    "Verified migration copy bytes",
    ["provider"],
    registry=registry,
)
COPIED_OBJECTS = Counter(
    "printstash_vault_migration_copied_objects_total",
    "Migration objects copied",
    ["provider"],
    registry=registry,
)
RETRIED_OBJECTS = Counter(
    "printstash_vault_migration_retried_objects_total",
    "Migration object retry attempts",
    ["provider"],
    registry=registry,
)


def transition(
    session: Session,
    run: VaultMigrationRun,
    state: str,
    *,
    error: str | None = None,
    retryable: bool = False,
) -> None:
    if state == run.state:
        return
    if state != "recovery_required" and state not in _TRANSITIONS.get(run.state, set()):
        raise ValueError("migration_phase_transition_forbidden")
    now = utcnow()
    history = json.loads(run.phase_history_json)
    provider = str(json.loads(run.destination_summary_json).get("provider", "unknown"))
    if history:
        PHASE_SECONDS.labels(run.state, provider).observe(
            max(
                0,
                (
                    now - ensure_utc(datetime.fromisoformat(history[-1]["at"]))
                ).total_seconds(),
            )
        )
    run.state, run.error_code, run.retryable = state, error, retryable
    run.last_activity_at = now
    history.append({"phase": state, "at": now.isoformat()})
    run.phase_history_json = json.dumps(history, separators=(",", ":"))
    session.add(run)
    session.add(
        AuditLog(
            actor_id=run.actor_id,
            action="vault.migration." + state,
            resource_type="vault_migration",
            diff_json=json.dumps(
                {"run_id": run.id, "phase": state, "error_code": error}
            ),
        )
    )
    verified = sum(
        obj.state in {"verified", "skipped"}
        for obj in session.exec(
            select(VaultMigrationObject).where(VaultMigrationObject.run_id == run.id)
        ).all()
    )
    delivered = enqueue_for_event(
        session,
        NotificationEventType.VAULT_MIGRATION,
        event_context={
            "migration_id": run.id,
            "migration_phase": state,
            "verified_objects": verified,
        },
    )
    if delivered:
        run.notification_events += 1
        session.add(run)


def audit_projection(session: Session, audit_id: int | None) -> dict | None:
    row = session.get(VaultAuditRun, audit_id) if audit_id is not None else None
    return (
        None
        if row is None
        else {
            "id": row.id,
            "state": row.state.value,
            "critical_count": row.critical_count,
            "warning_count": row.warning_count,
        }
    )


def health(session: Session) -> dict[str, object]:
    run = session.exec(
        select(VaultMigrationRun).order_by(VaultMigrationRun.created_at.desc()).limit(1)
    ).first()
    recovery = restore_in_progress()
    state = "recovery_required" if recovery else run.state if run else "idle"
    return {
        "ok": state not in {"paused", "failed", "recovery_required"},
        "state": state,
        "maintenance": recovery,
        "retryable": bool(run and run.retryable),
        "last_activity_at": run.last_activity_at if run else None,
    }


def project(session: Session, run: VaultMigrationRun) -> dict[str, object]:
    objects = list(
        session.exec(
            select(VaultMigrationObject).where(VaultMigrationObject.run_id == run.id)
        ).all()
    )
    final = [obj for obj in objects if obj.in_final]
    verified = [obj for obj in final if obj.state in {"verified", "skipped"}]
    copied = [obj for obj in final if obj.destination_receipt is not None]
    skipped = [obj for obj in final if obj.state == "skipped"]
    failed = [obj for obj in final if obj.state == "failed"]
    resources = []
    reservation = session.get(CapacityReservation, "vault-migration:" + run.id)
    if reservation:
        for value in json.loads(reservation.resources_json):
            resource = CapacityResource(**value)
            try:
                available = resource.probe()
            except OSError:
                available = None
            resources.append(
                {
                    "role": resource.role,
                    "required_bytes": resource.required_bytes,
                    "available_bytes": available,
                }
            )
    elapsed = max(
        1.0, (utcnow() - ensure_utc(run.started_at or run.created_at)).total_seconds()
    )
    totals: dict[str, dict] = defaultdict(lambda: {"objects": 0, "bytes": 0})
    for obj in final:
        totals[obj.resource_type]["objects"] += 1
        totals[obj.resource_type]["bytes"] += obj.size_bytes
    return {
        "id": run.id,
        "state": run.state,
        "plan_digest": run.plan_digest,
        "backup_summary": json.loads(run.backup_summary_json),
        "source": json.loads(run.source_summary_json),
        "destination": json.loads(run.destination_summary_json),
        "source_provider_ref": run.source_provider_ref,
        "destination_provider_ref": run.destination_provider_ref,
        "objects": len(final),
        "verified_objects": len(verified),
        "bytes": sum(obj.size_bytes for obj in final),
        "verified_bytes": sum(obj.size_bytes for obj in verified),
        "copied_objects": len(copied),
        "copied_bytes": sum(obj.size_bytes for obj in copied),
        "skipped_objects": len(skipped),
        "skipped_bytes": sum(obj.size_bytes for obj in skipped),
        "failed_objects": len(failed),
        "failed_bytes": sum(obj.size_bytes for obj in failed),
        "delta_objects": sum(not obj.baseline for obj in final),
        "throughput_bytes_per_second": sum(obj.size_bytes for obj in copied) / elapsed,
        "last_activity_at": run.last_activity_at,
        "error_code": run.error_code,
        "retryable": run.retryable,
        "policy": json.loads(run.policy_json),
        "phase_history": json.loads(run.phase_history_json),
        "pre_audit": audit_projection(session, run.pre_audit_id),
        "post_audit": audit_projection(session, run.post_audit_id),
        "full_audit": audit_projection(session, run.full_audit_id),
        "notification_events": run.notification_events,
        "cleanup_after": run.cleanup_after,
        "cleanup_findings": json.loads(run.cleanup_findings),
        "cleanup_outcome": run.cleanup_outcome,
        "source_retained": run.state != "cleaned",
        "recovery_required": restore_in_progress(),
        "expires_at": run.expires_at,
        "capacity_resources": resources,
        "capacity_warnings": ["capacity_unknown"]
        if any(r["available_bytes"] is None for r in resources)
        else [],
        "resource_kind_totals": [
            {"resource_type": kind, **counts} for kind, counts in sorted(totals.items())
        ],
        "recent_failures": [
            {"object_id": obj.id, "code": obj.error_code, "retryable": obj.retryable}
            for obj in failed
        ],
    }
