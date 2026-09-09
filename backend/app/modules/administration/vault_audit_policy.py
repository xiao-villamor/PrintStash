"""Calendar policies and atomic audit admission. Functions own their transactions."""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from time import sleep
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    AuditLog,
    RestoreMarker,
    SystemConfig,
    VaultAuditMode,
    VaultAuditPolicy,
    VaultAuditRun,
    VaultAuditRunState,
)
from app.modules.storage.storage_backend.runtime import get_backend

ACTIVE_STATES = (VaultAuditRunState.PENDING, VaultAuditRunState.RUNNING)
SAFE_REPAIR_ACTIONS = frozenset({"reparse_metadata", "regenerate_thumbnail"})


def window_start(day: date, start_time: str, timezone: str) -> datetime:
    """First occurrence in a fold; a gap advances to the first valid minute."""
    zone = ZoneInfo(timezone)
    wall = datetime.combine(day, time.fromisoformat(start_time))
    for minute in range(2881):
        candidate = (wall + timedelta(minutes=minute)).replace(tzinfo=zone, fold=0)
        utc = candidate.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return utc
    raise ValueError("audit_window_unavailable")


def next_slot(policy: VaultAuditPolicy, after: datetime) -> datetime:
    after = ensure_utc(after)
    day = after.astimezone(ZoneInfo(policy.timezone)).date()
    for offset in range(400):
        candidate_day = day + timedelta(days=offset)
        eligible = (
            candidate_day.weekday() == policy.weekday
            if policy.cadence == "weekly"
            else candidate_day.day
            == min(
                policy.month_day,
                calendar.monthrange(candidate_day.year, candidate_day.month)[1],
            )
        )
        if eligible:
            candidate = window_start(candidate_day, policy.start_time, policy.timezone)
            if candidate > after:
                return candidate
    raise ValueError("audit_schedule_unavailable")


def eligible_window(
    policy: VaultAuditPolicy, now: datetime
) -> tuple[datetime, datetime] | None:
    """Missed slots catch up in the next daily window, never in a burst."""
    now = ensure_utc(now)
    local_day = now.astimezone(ZoneInfo(policy.timezone)).date()
    for day in (local_day - timedelta(days=1), local_day):
        start = window_start(day, policy.start_time, policy.timezone)
        end = start + timedelta(minutes=policy.window_minutes)
        if start <= now < end:
            return start, end
    return None


def list_policies(session: Session) -> list[VaultAuditPolicy]:
    rows = {row.mode: row for row in session.exec(select(VaultAuditPolicy)).all()}
    return [
        rows.get(mode) or VaultAuditPolicy(mode=mode, cadence=cadence)
        for mode, cadence in (("quick", "weekly"), ("full", "monthly"))
    ]


def update_policy(
    session: Session,
    mode: VaultAuditMode,
    values: dict,
    actor_id: int,
    *,
    now: datetime | None = None,
) -> VaultAuditPolicy:
    now = ensure_utc(now or utcnow())
    values = dict(values)
    expected = values.pop("expected_revision", None)
    policy = session.get(VaultAuditPolicy, mode.value)
    if policy is not None:
        expected = policy.revision if expected is None else expected
        result = session.execute(
            update(VaultAuditPolicy)
            .where(
                col(VaultAuditPolicy.mode) == mode.value,
                col(VaultAuditPolicy.revision) == expected,
            )
            .values(revision=expected + 1)
            .execution_options(synchronize_session=False)
        )
        assert isinstance(result, CursorResult)
        if result.rowcount != 1:
            session.rollback()
            raise OperationError(
                "audit_policy_revision_conflict", kind=ErrorKind.CONFLICT
            )
        session.refresh(policy)
    elif expected not in (None, 1):
        raise OperationError("audit_policy_revision_conflict", kind=ErrorKind.CONFLICT)
    if policy is None:
        policy = VaultAuditPolicy(
            mode=mode.value,
            cadence="weekly" if mode == VaultAuditMode.QUICK else "monthly",
        )
    schedule_fields = (
        "cadence",
        "timezone",
        "weekday",
        "month_day",
        "start_time",
        "window_minutes",
    )
    old_schedule = tuple(getattr(policy, name) for name in schedule_fields)
    was_enabled = policy.enabled
    for key, value in values.items():
        if key in {"repair_actions", "notification_channels"}:
            setattr(policy, f"{key}_json", json.dumps(value))
        else:
            setattr(policy, key, value)
    if (
        policy.enabled
        and mode == VaultAuditMode.FULL
        and not policy.full_cost_acknowledged
    ):
        raise OperationError("full_audit_cost_acknowledgement_required")
    policy.requested_by = actor_id
    if policy not in session:
        policy.revision += 1
    policy.updated_at = now
    if not policy.enabled:
        policy.next_due_at = None
    elif (
        not was_enabled
        or policy.next_due_at is None
        or old_schedule != tuple(getattr(policy, name) for name in schedule_fields)
    ):
        policy.next_due_at = next_slot(policy, now)
    policy.deferred_reason = None
    session.add(
        AuditLog(
            action="audit.policy_updated",
            resource_type="vault_audit_policy",
            actor_id=actor_id,
            diff_json=json.dumps(
                {"mode": mode.value, "revision": policy.revision, "changes": values},
                default=str,
            ),
        )
    )
    session.add(policy)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise OperationError(
            "audit_policy_revision_conflict", kind=ErrorKind.CONFLICT
        ) from exc
    session.refresh(policy)
    return policy


def skip_once(
    session: Session,
    mode: VaultAuditMode,
    *,
    now: datetime | None = None,
    actor_id: int | None = None,
) -> VaultAuditPolicy:
    policy = session.get(VaultAuditPolicy, mode.value)
    if policy is None or policy.next_due_at is None:
        raise OperationError("audit_schedule_not_enabled", kind=ErrorKind.CONFLICT)
    policy.next_due_at = next_slot(
        policy, max(ensure_utc(now or utcnow()), ensure_utc(policy.next_due_at))
    )
    policy.deferred_reason = "skipped_once"
    policy.revision += 1
    session.add(
        AuditLog(
            action="audit.policy_skipped",
            resource_type="vault_audit_policy",
            actor_id=actor_id,
            diff_json=json.dumps({"mode": mode.value, "revision": policy.revision}),
        )
    )
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy


def storage_generation(session: Session) -> str:
    config = session.get(SystemConfig, 1)
    target = get_backend().storage_target
    markers = session.exec(select(RestoreMarker).order_by(col(RestoreMarker.id))).all()
    payload = [
        config.storage_identity if config else None,
        get_backend().namespace_for(
            get_backend().blob_key("audit-generation", 1, "probe")
        ),
        get_backend().namespace_for(get_backend().thumbnail_key(0)),
        target.target_ref if target else get_backend().backend_name,
        [
            (row.id, row.operation_nonce, row.state, row.updated_at.isoformat())
            for row in markers
        ],
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def admit_run(
    session: Session,
    actor_id: int,
    mode: VaultAuditMode,
    *,
    policy: VaultAuditPolicy | None = None,
    now: datetime | None = None,
    deadline: datetime | None = None,
) -> tuple[VaultAuditRun, bool]:
    for attempt in range(4):
        try:
            return _admit_once(
                session, actor_id, mode, policy=policy, now=now, deadline=deadline
            )
        except OperationalError as exc:
            session.rollback()
            if (
                session.get_bind().dialect.name != "sqlite"
                or "locked" not in str(exc).lower()
                or attempt == 3
            ):
                raise
            sleep(0.02 * (attempt + 1))
    raise RuntimeError("audit_admission_retry_exhausted")


def _admit_once(
    session: Session,
    actor_id: int,
    mode: VaultAuditMode,
    *,
    policy: VaultAuditPolicy | None = None,
    now: datetime | None = None,
    deadline: datetime | None = None,
) -> tuple[VaultAuditRun, bool]:
    """Unique active slot is the arbiter, with policy advancement in the same commit."""
    now = ensure_utc(now or utcnow())
    active = session.exec(
        select(VaultAuditRun).where(col(VaultAuditRun.state).in_(ACTIVE_STATES))
    ).first()
    if active is not None:
        return active, False
    run = VaultAuditRun(
        requested_by=actor_id,
        mode=mode,
        active_slot="audit",
        storage_generation=storage_generation(session),
        planned_bytes=estimated_owned_bytes(session, mode),
    )
    if policy is not None:
        assert policy.next_due_at is not None
        run.trigger = "scheduled"
        run.scheduled_for = policy.next_due_at
        run.trigger_key = f"{mode.value}:{policy.revision}:{ensure_utc(policy.next_due_at).isoformat()}"
        run.policy_revision = policy.revision
        run.deadline_at = deadline
        run.bytes_per_second = policy.bytes_per_second
        run.repair_actions_json = (
            policy.repair_actions_json if policy.auto_repair else "[]"
        )
        policy.launch_failures = 0
        policy.retry_after = None
        policy.last_attempt_at = now
        policy.next_due_at = next_slot(policy, now)
        policy.deferred_reason = None
        session.add(policy)
    session.add(run)
    session.add(
        AuditLog(
            action="audit.run_requested",
            resource_type="vault_audit_run",
            actor_id=actor_id,
            diff_json=json.dumps({"mode": mode.value, "trigger": run.trigger}),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        active = session.exec(
            select(VaultAuditRun).where(VaultAuditRun.active_slot == "audit")
        ).first()
        if active is None and run.trigger_key:
            active = session.exec(
                select(VaultAuditRun).where(
                    VaultAuditRun.trigger_key == run.trigger_key
                )
            ).first()
        if active is None:
            raise
        return active, False
    session.refresh(run)
    return run, True


def claim_due(
    session: Session, *, now: datetime | None = None, deferred_reason: str | None = None
) -> int | None:
    now = ensure_utc(now or utcnow())
    policies = session.exec(
        select(VaultAuditPolicy)
        .where(
            col(VaultAuditPolicy.enabled).is_(True),
            col(VaultAuditPolicy.paused).is_(False),
        )
        .order_by(col(VaultAuditPolicy.next_due_at))
    ).all()  # noqa: E712
    for policy in policies:
        if (
            policy.next_due_at is None
            or ensure_utc(policy.next_due_at) > now
            or policy.requested_by is None
        ):
            continue
        from app.modules.administration.vault_audit_events import record_overdue

        record_overdue(session, policy, now=now)
        if policy.retry_after is not None and ensure_utc(policy.retry_after) > now:
            continue
        window = eligible_window(policy, now)
        reason = deferred_reason or ("outside_window" if window is None else None)
        if (
            window
            and not reason
            and now < window[0] + timedelta(seconds=slot_jitter(policy))
        ):
            reason = "jitter"
        if reason:
            policy.deferred_reason = reason
            session.add(policy)
            session.commit()
            continue
        assert window is not None
        run, created = admit_run(
            session,
            policy.requested_by,
            VaultAuditMode(policy.mode),
            policy=policy,
            now=now,
            deadline=window[1],
        )
        if created:
            return run.id
        policy.deferred_reason = "audit_active"
        session.add(policy)
        session.commit()
        return None
    return None


def health(session: Session, *, now: datetime | None = None) -> dict:
    now = ensure_utc(now or utcnow())
    policies = list_policies(session)
    states = []
    for policy in policies:
        overdue = policy.next_due_at is not None and now > ensure_utc(
            policy.next_due_at
        ) + timedelta(minutes=policy.max_lateness_minutes)
        if policy.last_success_at is not None:
            overdue = overdue or now > next_slot(
                policy, policy.last_success_at
            ) + timedelta(minutes=policy.max_lateness_minutes)
        overdue = bool(policy.enabled and not policy.paused and overdue)
        latest = session.exec(
            select(VaultAuditRun)
            .where(VaultAuditRun.mode == VaultAuditMode(policy.mode))
            .order_by(col(VaultAuditRun.id).desc())
        ).first()
        states.append(
            {
                "enabled": policy.enabled,
                "paused": policy.paused,
                "last_success_age_seconds": max(
                    0, (now - ensure_utc(policy.last_success_at)).total_seconds()
                )
                if policy.last_success_at
                else None,
                "latest_result": latest.state.value if latest else None,
                "mode": policy.mode,
                "overdue": overdue,
                "last_success_at": policy.last_success_at,
                "next_due_at": policy.next_due_at,
                "deferred_reason": policy.deferred_reason,
            }
        )
    return {"ok": not any(row["overdue"] for row in states), "policies": states}


def estimated_remote_bytes(session: Session, mode: VaultAuditMode) -> int:
    """Known remote bytes in this audit; excludes unknown sizes and request costs."""
    from app.db.models import OwnedStorageObject, StorageObjectState

    rows = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
            OwnedStorageObject.backend != "local",
        )
    ).all()
    return sum(
        row.size_bytes or 0
        for row in rows
        if mode == VaultAuditMode.FULL or row.object_kind.startswith("thumbnail")
    )


def slot_jitter(policy: VaultAuditPolicy) -> int:
    """Stable per due slot and revision, bounded to leave at least one minute."""
    bound = min(policy.jitter_seconds, max(0, policy.window_minutes * 60 - 60))
    key = f"{policy.mode}:{policy.revision}:{policy.next_due_at}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % (bound + 1)


def defer_launch_failure(session: Session, *, now: datetime | None = None) -> None:
    """Bound transient launch retries; preserve due slots and overdue evidence."""
    now = ensure_utc(now or utcnow())
    for policy in session.exec(
        select(VaultAuditPolicy).where(col(VaultAuditPolicy.enabled).is_(True))
    ).all():
        if policy.next_due_at is None or ensure_utc(policy.next_due_at) > now:
            continue
        policy.launch_failures = min(6, policy.launch_failures + 1)
        policy.retry_after = now + timedelta(
            seconds=min(1800, 30 * 2**policy.launch_failures) + slot_jitter(policy)
        )
        policy.deferred_reason = "launch_retry"
        session.add(policy)
    session.commit()


def estimated_owned_bytes(session: Session, mode: VaultAuditMode) -> int:
    """Committed physical objects only; excludes externally referenced libraries."""
    from app.db.models import OwnedStorageObject, StorageObjectState

    rows = session.exec(
        select(OwnedStorageObject).where(
            OwnedStorageObject.state == StorageObjectState.COMMITTED
        )
    ).all()
    return sum(
        row.size_bytes or 0
        for row in rows
        if mode == VaultAuditMode.FULL or row.object_kind.startswith("thumbnail")
    )
