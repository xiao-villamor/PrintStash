"""Database-derived inventory; interactive reads never enumerate providers.

Logical bytes count references. Unique bytes count provider/namespace/key
identities with known size; unknown objects remain explicitly unmeasured.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SENTINEL_FILE_HASH,
    CapacityAdmissionEvent,
    CapacityReservation,
    Collection,
    File,
    Model,
    OwnedStorageObject,
    StagingLease,
    StorageDeleteIntent,
    StorageInventorySample,
    StorageObjectState,
    User,
    VaultAuditFinding,
    VaultAuditRun,
    VaultAuditRunState,
)
from app.db.scopes import live
from app.modules.library.model_views.access import accessible_live_model_ids_stmt
from app.modules.storage.capacity import CapacityResource
from app.modules.storage.capacity_policy import CapacityPolicy
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import provider_ref_for_backend
from app.modules.storage.storage_utils import ownership_snapshot


class InventoryBucket(BaseModel):
    category: str
    lifecycle: str
    count: int
    logical_bytes: int
    external_bytes: int = 0


class VolumeEvidence(BaseModel):
    domain_id: str
    roles: list[str]
    total_bytes: int | None = None
    free_bytes: int | None = None
    reserved_bytes: int = 0
    headroom_bytes: int = 0
    status: str = "unknown"
    method: str = "statvfs"


class ProviderCapacityEvidence(BaseModel):
    status: str = "unknown"
    total_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None
    quota_bytes: int | None = None
    measured_at: datetime | None = None
    method: str = "unsupported"
    reliability: str = "unknown"
    error: str | None = None


class AuditObservation(BaseModel):
    run_id: int
    completed_at: datetime
    unclaimed_object_count: int
    unclaimed_bytes: int | None = None
    unknown_size_count: int = 0
    method: str = "vault_audit_findings"


class CapacityActivity(BaseModel):
    active_reservations: list[dict]
    recent_denials: list[dict]


class StorageInventory(BaseModel):
    schema_version: int = 1
    generated_at: datetime
    target_ref: str
    buckets: list[InventoryBucket]
    logical_bytes: int
    external_referenced_bytes: int
    unique_owned_bytes: int
    unknown_object_count: int
    temporary_bytes: int
    backup_bytes: int
    measured_provider_bytes: int | None = None
    measured_at: datetime | None = None
    method: str = "database ownership census"
    confidence: str = "known sizes only"
    provider_capacity: ProviderCapacityEvidence = Field(
        default_factory=ProviderCapacityEvidence
    )
    latest_audit: AuditObservation | None = None
    volumes: list[VolumeEvidence] = Field(default_factory=list)


_PROVIDER_CAPACITY_STALE_AFTER = timedelta(hours=2)
_provider_capacity_locks: dict[str, threading.Lock] = {}
_provider_capacity_locks_guard = threading.Lock()
_provider_capacity_results: dict[str, tuple[float, ProviderCapacityEvidence]] = {}


def _capacity_lock(target_ref: str) -> threading.Lock:
    with _provider_capacity_locks_guard:
        return _provider_capacity_locks.setdefault(target_ref, threading.Lock())


def _stored_provider_capacity(
    latest: StorageInventorySample | None,
) -> ProviderCapacityEvidence:
    if latest is None:
        return ProviderCapacityEvidence()
    try:
        payload = StorageInventory.model_validate_json(latest.evidence_json)
    except (TypeError, ValueError):
        return ProviderCapacityEvidence(status="degraded", error="invalid_stored_evidence")
    evidence = payload.provider_capacity
    if evidence.measured_at is not None and evidence.status == "known":
        age = utcnow() - ensure_utc(evidence.measured_at)
        if age > _PROVIDER_CAPACITY_STALE_AFTER:
            return evidence.model_copy(update={"status": "stale"})
    return evidence


def _measure_provider_capacity(
    backend, target_ref: str, previous: ProviderCapacityEvidence
) -> ProviderCapacityEvidence:
    """Single-flight one bounded adapter probe; failures retain prior evidence."""
    requested_at = time.monotonic()
    with _capacity_lock(target_ref):
        completed = _provider_capacity_results.get(target_ref)
        if completed is not None and completed[0] >= requested_at:
            return completed[1]
        try:
            measured = backend.capacity()
        except Exception as exc:  # provider evidence is diagnostic, never authority
            evidence = previous.model_copy(
                update={"status": "degraded", "error": exc.__class__.__name__}
            )
        else:
            evidence = (
                ProviderCapacityEvidence()
                if measured is None
                else ProviderCapacityEvidence(
                    status="known",
                    total_bytes=measured.total_bytes,
                    used_bytes=measured.used_bytes,
                    available_bytes=measured.available_bytes,
                    quota_bytes=measured.quota_bytes,
                    measured_at=measured.measured_at,
                    method=measured.method,
                    reliability=measured.reliability.value,
                )
            )
        _provider_capacity_results[target_ref] = (time.monotonic(), evidence)
        return evidence


def _latest_audit_observation(session: Session) -> AuditObservation | None:
    run = session.exec(
        select(VaultAuditRun)
        .where(VaultAuditRun.state == VaultAuditRunState.COMPLETED)
        .order_by(col(VaultAuditRun.finished_at).desc())
        .limit(1)
    ).first()
    if run is None or run.id is None or run.finished_at is None:
        return None
    count = int(
        session.exec(
            select(func.count(col(VaultAuditFinding.id))).where(
                VaultAuditFinding.run_id == run.id,
                VaultAuditFinding.code == "unowned_blob_detected",
            )
        ).one()
    )
    return AuditObservation(
        run_id=run.id,
        completed_at=ensure_utc(run.finished_at),
        unclaimed_object_count=count,
        unclaimed_bytes=run.unclaimed_bytes,
        unknown_size_count=run.unclaimed_unknown_size_count,
    )


def _safe_operation_kind(operation_id: str) -> str:
    value = operation_id.partition(":")[0]
    return value if value.replace("-", "").isalnum() and len(value) <= 64 else "unknown"


def capacity_activity(session: Session) -> CapacityActivity:
    """Return bounded, identifier-free operator evidence for current admission."""
    active = []
    for row in session.exec(
        select(CapacityReservation)
        .order_by(col(CapacityReservation.created_at).desc())
        .limit(100)
    ):
        try:
            resources = [
                CapacityResource(**item) for item in json.loads(row.resources_json)
            ]
            identity = json.loads(row.owner_identity_json or "{}")
        except (TypeError, ValueError):
            resources = []
            identity = {}
        active.append(
            {
                "operation_kind": _safe_operation_kind(row.operation_id),
                "required_bytes": sum(item.required_bytes for item in resources),
                "roles": sorted({item.role for item in resources})[:8],
                "durable": identity.get("lifetime") == "workflow",
                "created_at": ensure_utc(row.created_at).isoformat(),
                "expires_at": ensure_utc(row.expires_at).isoformat(),
            }
        )
    denials = [
        {
            "operation_kind": row.operation_kind,
            "reason": row.reason,
            "required_bytes": row.required_bytes,
            "available_bytes": row.available_bytes,
            "reserved_bytes": row.reserved_bytes,
            "headroom_bytes": row.headroom_bytes,
            "occurred_at": ensure_utc(row.occurred_at).isoformat(),
        }
        for row in session.exec(
            select(CapacityAdmissionEvent)
            .where(CapacityAdmissionEvent.decision == "deny")
            .order_by(col(CapacityAdmissionEvent.occurred_at).desc())
            .limit(25)
        )
    ]
    return CapacityActivity(active_reservations=active, recent_denials=denials)


def cleanup_opportunities(session: Session) -> list[dict]:
    """Preview bounded candidates; execution remains with each safe owner."""
    now = utcnow()
    backend = get_backend()
    cache_namespace = backend.namespace_for(backend.stl_cache_key("0" * 64))
    cache_provider_ref = provider_ref_for_backend(backend, namespace=cache_namespace)
    staging_count, staging_bytes = session.exec(
        select(
            func.count(col(StagingLease.id)),
            func.coalesce(func.sum(col(StagingLease.size_bytes)), 0),
        ).where(StagingLease.expires_at <= now)
    ).one()
    if settings.trash_retention_days < 0:
        trash_count = trash_bytes = 0
    else:
        trash_cutoff = now - timedelta(days=settings.trash_retention_days)
        trash_count, trash_bytes = session.exec(
            select(
                func.count(func.distinct(col(Model.id))),
                func.coalesce(func.sum(col(File.size_bytes)), 0),
            )
            .join(File, col(File.model_id) == col(Model.id))
            .where(
                col(Model.deleted_at).is_not(None),
                Model.deleted_at <= trash_cutoff,  # pyright: ignore[reportOptionalOperand]
            )
        ).one()
    backup_cutoff = now - timedelta(days=max(0, settings.backup_retention_days))
    backup_count, backup_bytes = session.exec(
        select(
            func.count(col(OwnedStorageObject.id)),
            func.coalesce(func.sum(col(OwnedStorageObject.size_bytes)), 0),
        ).where(
            col(OwnedStorageObject.object_kind).startswith("backup"),
            OwnedStorageObject.created_at <= backup_cutoff,
        )
    ).one()
    cache_count, cache_bytes = session.exec(
        select(
            func.count(col(OwnedStorageObject.id)),
            func.coalesce(func.sum(col(OwnedStorageObject.size_bytes)), 0),
        ).where(
            col(OwnedStorageObject.object_kind).in_(["derived_stl_cache", "stl_cache"]),
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
            OwnedStorageObject.backend == backend.backend_name,
            OwnedStorageObject.namespace == cache_namespace,
            OwnedStorageObject.provider_ref == cache_provider_ref,
        )
    ).one()
    return [
        {
            "owner": "staging",
            "candidate_count": int(staging_count),
            "candidate_bytes": int(staging_bytes),
            "action": "cleanup_expired_staging",
            "available": True,
        },
        {
            "owner": "trash",
            "candidate_count": int(trash_count),
            "candidate_bytes": int(trash_bytes),
            "action": "review_expired_trash",
            "available": settings.trash_retention_days >= 0,
        },
        {
            "owner": "backups",
            "candidate_count": int(backup_count),
            "candidate_bytes": int(backup_bytes or 0),
            "action": "review_backup_retention",
            "available": True,
        },
        {
            "owner": "cache",
            "candidate_count": int(cache_count),
            "candidate_bytes": int(cache_bytes or 0),
            "action": "cleanup_derived_cache",
            "available": True,
        },
    ]


def _volumes(reserved: dict[str, int]) -> list[VolumeEvidence]:
    groups: dict[str, VolumeEvidence] = {}
    backend = get_backend()
    roots = [("staging", settings.staging_dir), ("backups", settings.backup_dir)]
    if backend.direct_path(backend.thumbnail_key(0)) is not None:
        roots.extend(
            [("vault", settings.data_dir), ("derivatives", settings.thumb_dir)]
        )
    else:
        groups["remote"] = VolumeEvidence(
            domain_id="remote", roles=["vault"], method="provider quota unavailable"
        )
    for role, root in roots:
        candidate = Path(root).resolve(strict=False)
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        try:
            info = candidate.stat()
            stats = os.statvfs(candidate)
            domain = f"volume:{info.st_dev}"
            if domain in groups:
                groups[domain].roles.append(role)
                continue
            remaining = stats.f_bavail * stats.f_frsize
            headroom = CapacityPolicy(
                settings.storage_min_free_bytes, settings.storage_min_free_percent
            ).headroom(stats.f_blocks * stats.f_frsize)
            claimed = reserved.get(domain, 0)
            groups[domain] = VolumeEvidence(
                domain_id=domain,
                roles=[role],
                total_bytes=stats.f_blocks * stats.f_frsize,
                free_bytes=remaining,
                reserved_bytes=claimed,
                headroom_bytes=headroom,
                status="blocked" if remaining - claimed < headroom else "available",
            )
        except OSError:
            groups[role] = VolumeEvidence(
                domain_id=role, roles=[role], status="unavailable"
            )
    return list(groups.values())


def inventory(
    session: Session,
    *,
    reserved: dict[str, int] | None = None,
    refresh_provider: bool = False,
) -> StorageInventory:
    backend = get_backend()
    target = backend.storage_target
    target_ref = (
        target.target_ref if target else provider_ref_for_backend(backend) or "unknown"
    )
    lifecycle = case((live(File) & live(Model), "live"), else_="trash")
    aggregates = session.execute(
        sa_select(
            col(File.file_type),
            lifecycle,
            col(File.is_external),
            func.count(col(File.id)),
            func.coalesce(func.sum(col(File.size_bytes)), 0),
        )
        .join(Model, col(Model.id) == col(File.model_id))
        .where(col(File.sha256) != SENTINEL_FILE_HASH)
        .group_by(col(File.file_type), lifecycle, col(File.is_external))
    ).all()
    buckets = [
        InventoryBucket(
            category=kind.value,
            lifecycle=state,
            count=count,
            logical_bytes=size,
            external_bytes=size if external else 0,
        )
        for kind, state, external, count, size in aggregates
    ]
    snapshot = ownership_snapshot(session, discover=False)
    objects: dict[tuple[str, str, str], int | None] = {}
    current_provider = provider_ref_for_backend(backend)
    receipts = list(session.exec(select(OwnedStorageObject)))
    pending_deletions = list(
        session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.status != "completed")
        )
    )
    owned_evidence = [*receipts, *pending_deletions]
    receipt_sizes = {
        (row.provider_ref or "legacy", row.namespace, row.key): row.size_bytes
        for row in owned_evidence
    }
    for blob in snapshot.primary + snapshot.derived + snapshot.embedded:
        try:
            namespace = backend.namespace_for(blob.key)
        except StorageCollisionError:
            # A legacy invalid locator is evidence of an unknown object, not
            # authority to stat or claim bytes outside the managed roots.
            objects[(current_provider, "unresolved", blob.key)] = None
            continue
        identity = (
            provider_ref_for_backend(backend, namespace=namespace),
            namespace,
            blob.key,
        )
        known_size = blob.expected_size
        if known_size is None:
            known_size = receipt_sizes.get(identity)
        if identity not in objects or objects[identity] is None:
            objects[identity] = known_size
        if blob.resource_type != "file":
            buckets.append(
                InventoryBucket(
                    category=blob.resource_type,
                    lifecycle="owned",
                    count=1,
                    logical_bytes=known_size or 0,
                )
            )
    backup_bytes = 0
    backup_count = 0
    for row in owned_evidence:
        identity = (row.provider_ref or "legacy", row.namespace, row.key)
        if row.size_bytes is not None:
            objects[identity] = row.size_bytes
        else:
            objects.setdefault(identity, None)
        if row.object_kind.startswith("backup"):
            backup_bytes += row.size_bytes or 0
            backup_count += 1
    temporary = int(
        session.exec(
            select(func.coalesce(func.sum(col(StagingLease.size_bytes)), 0))
        ).one()
    )
    buckets.extend(
        [
            InventoryBucket(
                category="staging",
                lifecycle="temporary",
                count=int(session.exec(select(func.count(col(StagingLease.id)))).one()),
                logical_bytes=temporary,
            ),
            InventoryBucket(
                category="backups",
                lifecycle="replica",
                count=backup_count,
                logical_bytes=backup_bytes,
            ),
        ]
    )
    # Keep the interactive payload bounded by categories, not library size.
    grouped: dict[tuple[str, str], InventoryBucket] = {}
    for bucket in buckets:
        key = (bucket.category, bucket.lifecycle)
        if key not in grouped:
            grouped[key] = bucket.model_copy()
        else:
            grouped[key].count += bucket.count
            grouped[key].logical_bytes += bucket.logical_bytes
            grouped[key].external_bytes += bucket.external_bytes
    buckets = list(grouped.values())
    latest = session.exec(
        select(StorageInventorySample)
        .where(col(StorageInventorySample.target_ref) == target_ref)
        .order_by(col(StorageInventorySample.sampled_at).desc())
        .limit(1)
    ).first()
    provider_capacity = _stored_provider_capacity(latest)
    if refresh_provider:
        provider_capacity = _measure_provider_capacity(
            backend, target_ref, provider_capacity
        )
    return StorageInventory(
        generated_at=utcnow(),
        target_ref=target_ref,
        buckets=buckets,
        logical_bytes=sum(
            bucket.logical_bytes
            for bucket in buckets
            if bucket.lifecycle not in {"temporary", "replica"}
        ),
        external_referenced_bytes=sum(bucket.external_bytes for bucket in buckets),
        unique_owned_bytes=sum(size for size in objects.values() if size is not None),
        unknown_object_count=sum(size is None for size in objects.values()),
        temporary_bytes=temporary,
        backup_bytes=backup_bytes,
        measured_provider_bytes=provider_capacity.used_bytes,
        measured_at=provider_capacity.measured_at,
        provider_capacity=provider_capacity,
        latest_audit=_latest_audit_observation(session),
        volumes=_volumes(reserved or {}),
    )


def record_sample(session: Session, current: StorageInventory) -> None:
    """Keep hourly recent evidence, then one daily row for bounded old history."""
    hour = current.generated_at.replace(minute=0, second=0, microsecond=0)
    rows = list(
        session.exec(
            select(StorageInventorySample)
            .where(col(StorageInventorySample.target_ref) == current.target_ref)
            .order_by(col(StorageInventorySample.sampled_at).desc())
        )
    )
    _observe_prediction_error(rows, current)
    existing = next(
        (
            row
            for row in rows
            if ensure_utc(row.sampled_at).replace(minute=0, second=0, microsecond=0)
            == hour
        ),
        None,
    )
    sample = existing or StorageInventorySample(
        target_ref=current.target_ref,
        owned_bytes=current.unique_owned_bytes,
        evidence_json="{}",
    )
    sample.sampled_at = current.generated_at
    sample.owned_bytes = current.unique_owned_bytes
    sample.evidence_json = current.model_dump_json()
    session.add(sample)
    high_resolution_cutoff = current.generated_at - timedelta(days=14)
    retention_cutoff = current.generated_at - timedelta(days=366)
    retained_old_days: set[object] = set()
    for row in rows:
        if row is existing:
            continue
        sampled = ensure_utc(row.sampled_at)
        remove = sampled < retention_cutoff
        if not remove and sampled < high_resolution_cutoff:
            day = sampled.date()
            remove = day in retained_old_days
            retained_old_days.add(day)
        if remove:
            session.delete(row)
    session.commit()


def _observe_prediction_error(
    rows: list[StorageInventorySample], current: StorageInventory
) -> None:
    if not rows:
        return
    prior = growth_forecast(
        [(ensure_utc(row.sampled_at), row.owned_bytes) for row in rows], None
    )
    rate = prior.get("bytes_per_day")
    latest = rows[0]
    elapsed_days = (
        current.generated_at - ensure_utc(latest.sampled_at)
    ).total_seconds() / 86400
    if rate is None or rate <= 0 or elapsed_days <= 0:
        return
    predicted = latest.owned_bytes + rate * elapsed_days
    ratio = abs(predicted - current.unique_owned_bytes) / max(
        current.unique_owned_bytes, 1
    )
    from app.modules.storage.capacity_observability import record_prediction_error

    record_prediction_error(ratio)


def growth_forecast(
    samples: list[tuple[datetime, int]], available_bytes: int | None
) -> dict:
    ordered = sorted(samples)
    daily = {
        ensure_utc(when).date(): (ensure_utc(when), size) for when, size in ordered
    }
    ordered = sorted(daily.values())
    sample_count = len(ordered)
    window_days = (
        (ordered[-1][0] - ordered[0][0]).total_seconds() / 86400
        if len(ordered) > 1
        else 0
    )
    common = {
        "sample_count": sample_count,
        "window_days": window_days,
        "confidence": "insufficient",
        "threshold_at": None,
    }
    if sample_count < 7 or window_days < 7:
        return {
            "status": "insufficient_data",
            "days_remaining": None,
            "bytes_per_day": None,
            **common,
        }
    rates = [
        (right[1] - left[1]) / ((right[0] - left[0]).total_seconds() / 86400)
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    rate = median(rates)
    if rate <= 0:
        return {
            "status": "no_positive_growth",
            "days_remaining": None,
            "bytes_per_day": rate,
            **common,
        }
    if max(rates) > rate * 10 or min(rates) < -rate * 10:
        return {
            "status": "unstable_growth",
            "days_remaining": None,
            "bytes_per_day": rate,
            **common,
        }
    days_remaining = (
        max(0, available_bytes / rate) if available_bytes is not None else None
    )
    confidence = "high" if sample_count >= 14 and window_days >= 14 else "medium"
    return {
        "status": "estimated" if available_bytes is not None else "capacity_unknown",
        "days_remaining": days_remaining,
        "bytes_per_day": rate,
        **common,
        "confidence": confidence,
        "threshold_at": (
            (ordered[-1][0] + timedelta(days=days_remaining)).isoformat()
            if days_remaining is not None
            else None
        ),
    }


def history(session: Session, target_ref: str) -> list[dict]:
    rows = session.exec(
        select(StorageInventorySample)
        .where(col(StorageInventorySample.target_ref) == target_ref)
        .order_by(col(StorageInventorySample.sampled_at).desc())
        .limit(24 * 14 + 366)
    )
    result = []
    for row in rows:
        categories = {
            "live_originals": row.owned_bytes,
            "trash": 0,
            "derived_cache": 0,
            "backups": 0,
        }
        try:
            evidence = StorageInventory.model_validate_json(row.evidence_json)
            categories = _history_categories(evidence)
        except (TypeError, ValueError):
            pass
        result.append(
            {
                "sampled_at": ensure_utc(row.sampled_at).isoformat(),
                "owned_bytes": row.owned_bytes,
                "categories": categories,
            }
        )
    return result


def _history_categories(current: StorageInventory) -> dict[str, int]:
    artifact_types = {"stl", "3mf", "obj", "step", "dxf", "gcode", "other"}
    derived_types = {
        "thumbnail",
        "legacy_thumbnail",
        "derived_stl",
        "cache",
        "document_image",
        "collection_image",
    }
    return {
        "live_originals": sum(
            bucket.logical_bytes
            for bucket in current.buckets
            if bucket.lifecycle == "live" and bucket.category in artifact_types
        ),
        "trash": sum(
            bucket.logical_bytes
            for bucket in current.buckets
            if bucket.lifecycle == "trash"
        ),
        "derived_cache": sum(
            bucket.logical_bytes
            for bucket in current.buckets
            if bucket.category in derived_types
        ),
        "backups": current.backup_bytes,
    }


def logical_drilldown(
    session: Session,
    user: User,
    *,
    offset: int = 0,
    limit: int = 50,
    collection_id: int | None = None,
) -> list[dict]:
    visible = accessible_live_model_ids_stmt(session, user)
    if collection_id is not None:
        visible = visible.where(
            col(Model.collection_id).is_(None)
            if collection_id == 0
            else col(Model.collection_id) == collection_id
        )
    rows = session.execute(
        sa_select(
            col(Model.id),
            col(Model.name),
            func.coalesce(func.sum(col(File.size_bytes)), 0),
        )
        .join(File, col(File.model_id) == col(Model.id))
        .where(col(Model.id).in_(visible), live(File))
        .group_by(col(Model.id), col(Model.name))
        .order_by(func.sum(col(File.size_bytes)).desc(), col(Model.id))
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {"model_id": identity, "name": name, "logical_bytes": size}
        for identity, name, size in rows
    ]


def cleanup_expired_staging(session: Session, actor: User) -> dict:
    """Explicit cleanup delegates receipt validation to the existing owner."""
    from app.modules.administration import audit
    from app.modules.ingestion.staging_cleanup import prune_expired
    from app.modules.storage.capacity_observability import record_cleanup

    try:
        removed, unlinked = prune_expired(session, backend=get_backend())
    except Exception:
        record_cleanup("staging", "error")
        raise
    session.commit()
    audit.record(
        session,
        action="storage.cleanup_expired_staging",
        resource_type="storage",
        actor_id=actor.id,
        diff={"leases_removed": removed, "files_removed": unlinked},
    )
    current = inventory(session)
    record_sample(session, current)
    record_cleanup("staging", "success")
    return {"leases_removed": removed, "files_removed": unlinked, "inventory": current}


def cleanup_derived_cache(session: Session, actor: User) -> dict:
    """Queue deletion only for exact, receipt-verified rebuildable STL caches."""
    from app.modules.administration import audit
    from app.modules.storage.capacity_observability import record_cleanup
    from app.modules.storage.storage_deletion import (
        enqueue_owned_key,
        process_storage_delete_intents,
    )

    backend = get_backend()
    cache_namespace = backend.namespace_for(backend.stl_cache_key("0" * 64))
    cache_provider_ref = provider_ref_for_backend(backend, namespace=cache_namespace)
    candidates = list(
        session.exec(
            select(OwnedStorageObject)
            .where(
                col(OwnedStorageObject.object_kind).in_(["derived_stl_cache", "stl_cache"]),
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
                OwnedStorageObject.backend == backend.backend_name,
                OwnedStorageObject.namespace == cache_namespace,
                OwnedStorageObject.provider_ref == cache_provider_ref,
            )
            .order_by(col(OwnedStorageObject.id))
            .limit(10_000)
        )
    )
    enqueued = 0
    for candidate in candidates:
        if enqueue_owned_key(
            session,
            backend,
            candidate.key,
            resource_kind="derived_stl_cache",
            resource_id=candidate.id,
        ):
            enqueued += 1
    session.commit()
    process_storage_delete_intents(limit=max(100, enqueued))
    audit.record(
        session,
        action="storage.cleanup_derived_cache",
        resource_type="storage",
        actor_id=actor.id,
        diff={
            "candidates": len(candidates),
            "enqueued": enqueued,
        },
    )
    current = inventory(session)
    record_sample(session, current)
    record_cleanup("cache", "success" if enqueued == len(candidates) else "error")
    return {
        "candidates": len(candidates),
        "enqueued": enqueued,
        "inventory": current,
    }


def legacy_usage(session: Session) -> dict:
    """Legacy dashboard wire shape, sourced from recorded owned object sizes."""
    count, size = session.exec(
        select(
            func.count(col(OwnedStorageObject.id)),
            func.coalesce(func.sum(col(OwnedStorageObject.size_bytes)), 0),
        )
    ).one()
    return {
        "backend": settings.storage_backend,
        "object_count": count,
        "total_size_bytes": size,
        "ok": True,
    }


def refresh_inventory_sample(session_factory) -> None:
    """Hourly maintenance records one daily sample and reconciles dead owners."""
    from app.modules.storage.capacity import CapacityManager

    manager = CapacityManager(session_factory)
    manager.reconcile_stopped_processes()
    with session_factory.scoped_session() as session:
        record_sample(
            session,
            inventory(
                session,
                reserved=manager.reserved_bytes(),
                refresh_provider=True,
            ),
        )


class InventoryReport(BaseModel):
    inventory: StorageInventory
    history: list[dict]
    forecast: dict


def inventory_report(session: Session, manager) -> InventoryReport:
    started = time.monotonic()
    try:
        current = inventory(session, reserved=manager.reserved_bytes())
        samples = history(session, current.target_ref)
    except Exception:
        from app.modules.storage.capacity_observability import inventory_duration

        inventory_duration.labels("error", "interactive").observe(
            time.monotonic() - started
        )
        raise
    available = next(
        (
            max(0, volume.free_bytes - volume.reserved_bytes - volume.headroom_bytes)
            for volume in current.volumes
            if "vault" in volume.roles and volume.free_bytes is not None
        ),
        None,
    )
    report = InventoryReport(
        inventory=current,
        history=samples,
        forecast=growth_forecast(
            [
                (datetime.fromisoformat(row["sampled_at"]), row["owned_bytes"])
                for row in samples
            ],
            available,
        ),
    )
    from app.modules.storage.capacity_observability import (
        inventory_duration,
        observe_inventory,
    )

    inventory_duration.labels("success", "interactive").observe(
        time.monotonic() - started
    )
    observe_inventory(current, provider=get_backend().transport)
    return report


def collection_drilldown(
    session: Session, user: User, *, offset: int = 0, limit: int = 50
) -> list[dict]:
    """Attribute logical references only within the caller's live Model scope."""
    visible = accessible_live_model_ids_stmt(session, user)
    rows = session.execute(
        sa_select(
            col(Model.collection_id),
            col(Collection.name),
            func.coalesce(func.sum(col(File.size_bytes)), 0),
            func.coalesce(
                func.sum(case((col(File.is_external), col(File.size_bytes)), else_=0)),
                0,
            ),
            func.count(func.distinct(col(Model.id))),
        )
        .join(File, col(File.model_id) == col(Model.id))
        .outerjoin(Collection, col(Collection.id) == col(Model.collection_id))
        .where(
            col(Model.id).in_(visible),
            live(File),
            col(File.sha256) != SENTINEL_FILE_HASH,
        )
        .group_by(col(Model.collection_id), col(Collection.name))
        .order_by(func.sum(col(File.size_bytes)).desc(), col(Model.collection_id))
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "collection_id": identity,
            "name": name or "Uncollected",
            "logical_bytes": size,
            "external_bytes": external,
            "model_count": count,
        }
        for identity, name, size, external, count in rows
    ]
