"""Durable create-only Vault copy, verified activation and receipt-scoped cleanup."""

from __future__ import annotations

import hashlib
import json
import tempfile
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from threading import RLock

from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ArtifactUploadSession,
    CapacityReservation,
    CaptureUploadSlot,
    File,
    Model,
    ModelSourceCover,
    StagingLease,
    SystemConfig,
    ThumbnailGeneration,
    VaultGeneration,
    VaultMigrationObject,
    VaultMigrationRun,
)
from app.db.session import SessionFactory
from app.modules.administration.runtime_config import (
    activate_config,
    update_storage_provider,
)
from app.modules.storage import migration_journal
from app.modules.storage.capacity import (
    CapacityManager,
    CapacityReservationHandle,
    CapacityResource,
)
from app.modules.storage.migration_backup import verify_migration_backup as _backup
from app.modules.storage.migration_census import census
from app.modules.storage.migration_identity import namespace_ref, validate_isolation
from app.modules.storage.migration_progress import transition
from app.modules.storage.storage_backend import generations
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
)
from app.modules.storage.storage_backend.factory import build_configured_backend
from app.modules.storage.storage_backend.local import (
    LocalStorageBackend,
    enroll_legacy_local_root,
)
from app.modules.storage.storage_backend.runtime import get_bound_backend
from app.modules.storage.storage_ownership import (
    matching_creation_receipt,
    record_creation,
)
from app.modules.storage.storage_providers import (
    parse_provider_config,
    split_provider_config,
)
from app.runtime.maintenance import (
    activating_storage_configuration,
    allow_retained_destination_destruction,
    begin_restore_maintenance,
    end_restore_maintenance,
    hold_restore_maintenance,
    restore_in_progress,
    retain_storage_objects,
)
from app.schemas.vault_migration import MigrationPolicy

_lock = RLock()
_retentions: dict[str, AbstractContextManager] = {}


@contextmanager
def _drained_recovery():
    """Recovery proves authority against a stable catalogue, even on manual retry."""
    begin_restore_maintenance()
    try:
        yield
    except BaseException:
        hold_restore_maintenance()
        raise


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _hash(backend: StorageBackend, key: str) -> tuple[int, str]:
    count = 0
    digest = hashlib.sha256()
    for chunk in backend.stream_chunks(key):
        count += len(chunk)
        digest.update(chunk)
    return count, digest.hexdigest()


def _source_config(session: Session) -> str:
    stored = session.get(SystemConfig, 1)
    if stored and stored.storage_provider:
        payload = {
            **json.loads(stored.storage_provider_config_json or "{}"),
            **json.loads(stored.storage_provider_secret_json or "{}"),
        }
    elif settings.storage_provider_config:
        payload = json.loads(str(settings.storage_provider_config))
    elif settings.storage_backend == "local":
        payload = {
            "provider": "local",
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        }
    elif settings.storage_backend == "s3":
        payload = {
            "provider": "s3",
            "bucket": settings.s3_bucket,
            "endpoint_url": settings.s3_endpoint_url,
            "region": settings.s3_region,
            "access_key": settings.s3_access_key,
            "secret_key": settings.s3_secret_key,
            "root": settings.s3_root,
        }
    else:
        raise ValueError("migration_source_configuration_unavailable")
    return _json(parse_provider_config(payload).model_dump(mode="json"))


def _retain(run_id: str) -> None:
    if run_id not in _retentions:
        guard = retain_storage_objects()
        guard.__enter__()
        _retentions[run_id] = guard


def _release(run_id: str) -> None:
    guard = _retentions.pop(run_id, None)
    if guard:
        guard.__exit__(None, None, None)


def _objects(session: Session, run_id: str) -> list[VaultMigrationObject]:
    return list(
        session.exec(
            select(VaultMigrationObject).where(VaultMigrationObject.run_id == run_id)
        ).all()
    )


class VaultMigrations:
    def __init__(self, factory: SessionFactory):
        self.factory = factory
        self.capacity = CapacityManager(factory)

    def get(self, run_id: str) -> dict[str, object]:
        from app.modules.storage.migration_progress import project

        with self.factory.scoped_session() as session:
            return project(session, self._run(session, run_id))

    def list(self) -> list[dict[str, object]]:
        with self.factory.scoped_session() as session:
            ids = [
                run.id
                for run in sorted(
                    session.exec(select(VaultMigrationRun)).all(),
                    key=lambda run: run.created_at,
                    reverse=True,
                )
            ]
        return [self.get(run_id) for run_id in ids]

    @staticmethod
    def _run(session: Session, run_id: str) -> VaultMigrationRun:
        run = session.get(VaultMigrationRun, run_id)
        if run is None:
            raise ValueError("migration_not_found")
        return run

    def preflight(
        self,
        destination: dict,
        *,
        backup_id: str,
        backup_source_ref: str | None = None,
        actor_id: int | None = None,
        policy: dict | None = None,
    ) -> dict[str, object]:
        with _lock:
            if restore_in_progress():
                raise ValueError("migration_recovery_required")
            with self.factory.scoped_session() as session:
                if any(
                    run.state not in {"active", "cleaned", "discarded", "complete"}
                    for run in session.exec(select(VaultMigrationRun)).all()
                ):
                    raise ValueError("migration_already_running")
            backup_summary = _backup(backup_id, backup_source_ref)
            migration_policy = MigrationPolicy.model_validate(policy or {})
            candidate_config = parse_provider_config(destination)
            candidate = build_configured_backend(candidate_config)
            source = get_bound_backend()
            validate_isolation(source, candidate)
            if not source.health_probe().get("ok"):
                raise ValueError("migration_source_unhealthy")
            pre_audit_id = self._run_audit(actor_id)
            # A configured mount must already exist; never create a shadow root
            # when the operator intended an unavailable mounted filesystem.
            if isinstance(candidate, LocalStorageBackend):
                for role, root in (
                    ("data", candidate.data_dir),
                    ("thumb", candidate.thumb_dir),
                ):
                    if not root.is_dir():
                        raise ValueError("migration_destination_root_unavailable")
                    if any(
                        p.name != ".printstash-storage-root.json"
                        for p in root.iterdir()
                    ):
                        raise ValueError("migration_destination_not_empty")
                    if not enroll_legacy_local_root(
                        root,
                        role=role,
                        installation=str(settings.storage_identity),
                        proofs=[],
                        allow_empty=True,
                    ):
                        raise ValueError("migration_destination_binding_invalid")
            elif list(candidate.walk_keys()):
                raise ValueError("migration_destination_not_empty")
            candidate.ensure_setup()
            if (
                not candidate.capabilities.conditional_create
                or not candidate.capabilities.verified_delete
            ):
                raise ValueError("migration_destination_exact_receipts_required")
            with self.factory.scoped_session() as session:
                if any(
                    run.state not in {"active", "cleaned", "discarded", "complete"}
                    for run in session.exec(select(VaultMigrationRun)).all()
                ):
                    raise ValueError("migration_already_running")
                source_config = _source_config(session)
                destination_config = _json(candidate_config.model_dump(mode="json"))
                source_digest = _digest(source_config)
                if source_config == destination_config:
                    raise ValueError("migration_destination_matches_source")
                run = VaultMigrationRun(
                    actor_id=actor_id,
                    source_epoch=generations.current_epoch(),
                    source_config=source_config,
                    destination_config=destination_config,
                    source_digest=source_digest,
                    plan_digest=_digest(
                        source_digest
                        + destination_config
                        + backup_id
                        + migration_policy.model_dump_json()
                    ),
                    backup_id=backup_id,
                    backup_source_ref=backup_source_ref,
                    backup_summary_json=_json(backup_summary),
                    expires_at=utcnow() + timedelta(minutes=30),
                    policy_json=migration_policy.model_dump_json(),
                    source_provider_ref=namespace_ref(source),
                    destination_provider_ref=namespace_ref(candidate),
                    source_summary_json=_json(
                        split_provider_config(
                            parse_provider_config(json.loads(source_config))
                        )[0]
                    ),
                    destination_summary_json=_json(
                        split_provider_config(candidate_config)[0]
                    ),
                    pre_audit_id=pre_audit_id,
                    phase_history_json=_json(
                        [{"phase": "planned", "at": utcnow().isoformat()}]
                    ),
                )
                session.add(run)
                session.flush()
                with retain_storage_objects():
                    self._census(session, run, source, candidate)
                session.commit()
                run_id = run.id
            try:
                self._reserve(run_id, candidate)
            except Exception:
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    transition(
                        session,
                        run,
                        "discarded",
                        error="migration_capacity_unavailable",
                    )
                    session.commit()
                raise
            return self.get(run_id)

    def _run_audit(self, actor_id: int | None, *, full: bool = False) -> int:
        from app.db.models import VaultAuditMode, VaultAuditRun, VaultAuditRunState
        from app.modules.administration import vault_audit

        if actor_id is None:
            raise ValueError("migration_actor_required")
        with self.factory.scoped_session() as session:
            audit, created = vault_audit.create_run(
                session, actor_id, VaultAuditMode.FULL if full else VaultAuditMode.QUICK
            )
            if not created or audit.id is None:
                raise ValueError("migration_audit_busy")
            audit_id = audit.id
        vault_audit.execute_run(audit_id)
        with self.factory.scoped_session() as session:
            result = session.get(VaultAuditRun, audit_id)
            if (
                result is None
                or result.state != VaultAuditRunState.COMPLETED
                or result.critical_count
            ):
                raise ValueError("migration_audit_failed")
        return audit_id

    def _census(
        self,
        session: Session,
        run: VaultMigrationRun,
        source: StorageBackend,
        candidate: StorageBackend,
        *,
        final: bool = False,
    ) -> None:
        known = {row.source_key: row for row in _objects(session, run.id)}
        if final:
            for row in known.values():
                row.in_final = False
                session.add(row)
        for blob in census(session, source, candidate):
            actual_size, actual_hash = _hash(source, blob.source_key)
            if blob.expected_size is not None and actual_size != blob.expected_size:
                raise ValueError("migration_source_size_mismatch")
            if blob.expected_sha256 and actual_hash != blob.expected_sha256:
                raise ValueError("migration_source_hash_mismatch")
            if len(blob.destination_key.encode("utf-8")) > 2048:
                raise ValueError("migration_destination_key_too_long")
            if candidate.storage_target and candidate.storage_target.transport == "s3":
                if (
                    len(blob.destination_key.encode("utf-8")) > 1024
                    or actual_size > 5 * 1024**4
                ):
                    raise ValueError("migration_destination_limits_exceeded")
            receipt = matching_creation_receipt(session, source, blob.source_key)
            if receipt is None and blob.resource_type == "artifact_upload_staging":
                upload = session.get(ArtifactUploadSession, blob.resource_id)
                if upload:
                    from app.modules.ingestion.artifact_uploads.native_parts import (
                        NativeMultipartUploadAdapter,
                    )

                    claimed = NativeMultipartUploadAdapter.completion_receipt(upload)
                    if (
                        claimed
                        and claimed.key == blob.source_key
                        and source.creation_matches(claimed)
                    ):
                        receipt = claimed
            prior = known.get(blob.source_key)
            if prior is not None:
                prior.in_final = True
                if final and (prior.size_bytes, prior.sha256) != (
                    actual_size,
                    actual_hash,
                ):
                    prior.size_bytes, prior.sha256 = actual_size, actual_hash
                    prior.source_receipt = _json(asdict(receipt)) if receipt else None
                    prior.state = "pending"
                session.add(prior)
                continue
            if candidate.exists(blob.destination_key):
                raise ValueError("migration_destination_collision")
            session.add(
                VaultMigrationObject(
                    run_id=run.id,
                    source_key_digest=_digest(blob.source_key),
                    source_key=blob.source_key,
                    destination_key=blob.destination_key,
                    resource_type=blob.resource_type,
                    resource_id=blob.resource_id,
                    size_bytes=actual_size,
                    sha256=actual_hash,
                    baseline=not final,
                    source_receipt=_json(asdict(receipt)) if receipt else None,
                )
            )

    def _reserve(self, run_id: str, candidate: StorageBackend):
        with self.factory.scoped_session() as session:
            objects = _objects(session, run_id)
            policy = MigrationPolicy.model_validate_json(
                self._run(session, run_id).policy_json
            )
            largest = sum(
                sorted(
                    (obj.size_bytes for obj in objects if obj.in_final), reverse=True
                )[: policy.concurrency]
            )
            objects = [obj for obj in objects if obj.destination_receipt is None]
            total = sum(obj.size_bytes for obj in objects)
        resources = [
            CapacityResource.for_path(
                Path(tempfile.gettempdir()),
                0 if isinstance(candidate, LocalStorageBackend) else largest,
                role="migration-scratch",
            )
        ]
        if isinstance(candidate, LocalStorageBackend):
            # Reserve both roots' peak allocations on the actual volumes;
            # existing source and retained copies already reduce observed free space.
            data = sum(
                obj.size_bytes
                for obj in objects
                if obj.destination_key.startswith(str(candidate.data_dir) + "/")
            )
            resources += [
                CapacityResource.for_path(
                    candidate.data_dir, data, role="migration-data"
                ),
                CapacityResource.for_path(
                    candidate.thumb_dir, total - data, role="migration-thumbnails"
                ),
            ]
        else:
            target = candidate.storage_target
            if target is None:
                raise ValueError("migration_destination_identity_required")
            measured = candidate.capacity()
            resources += [
                CapacityResource.for_quota(
                    target.target_ref,
                    total,
                    measured.available_bytes if measured else None,
                    role="migration-destination",
                )
            ]
        operation_id = "vault-migration:" + run_id
        with self.factory.scoped_session() as session:
            existing = session.get(CapacityReservation, operation_id)
        if existing is not None:
            handle = CapacityReservationHandle(
                self.capacity, operation_id, resources, ()
            )
            handle.renew()
            return handle
        return self.capacity.reserve(operation_id, resources, durable=True)

    def start(self, run_id: str, plan_digest: str) -> dict[str, object]:
        with _lock, self.factory.scoped_session() as session:
            run = self._run(session, run_id)
            if (
                run.state != "planned"
                or run.plan_digest != plan_digest
                or ensure_utc(run.expires_at) < utcnow()
            ):
                raise ValueError("migration_plan_stale")
            if (
                _digest(_source_config(session)) != run.source_digest
                or generations.current_epoch() != run.source_epoch
            ):
                raise ValueError("migration_source_generation_changed")
            _retain(run.id)
            source = get_bound_backend()
            candidate = build_configured_backend(
                parse_provider_config(json.loads(run.destination_config))
            )
            self._census(session, run, source, candidate)
            migration_journal.append(
                {
                    "phase": "baseline",
                    "run_id": run.id,
                    "nonce": run.journal_nonce,
                    "source_epoch": run.source_epoch,
                }
            )
            run.started_at = utcnow()
            transition(session, run, "copying")
            session.commit()
        return self.get(run_id)

    def pause(self, run_id: str) -> dict[str, object]:
        with self.factory.scoped_session() as session:
            run = self._run(session, run_id)
            if run.state not in {"copying", "ready", "paused"}:
                raise ValueError("migration_cannot_pause")
            transition(session, run, "paused", retryable=True)
            session.commit()
        return self.get(run_id)

    def resume(self, run_id: str, *, failed_only: bool = False) -> dict[str, object]:
        with _lock, self.factory.scoped_session() as session:
            run = self._run(session, run_id)
            if restore_in_progress():
                raise ValueError("migration_recovery_required")
            if run.state not in {"paused", "failed", "ready"}:
                raise ValueError("migration_cannot_resume")
            if (
                _digest(_source_config(session)) != run.source_digest
                or generations.current_epoch() != run.source_epoch
            ):
                raise ValueError("migration_source_generation_changed")
            for obj in _objects(session, run_id):
                if failed_only and obj.state != "failed":
                    continue
                if failed_only and not obj.retryable:
                    continue
                obj.state = "copied" if obj.destination_receipt else "pending"
                obj.error_code = None
                session.add(obj)
            _retain(run_id)
            transition(session, run, "copying")
            session.commit()
        return self.get(run_id)

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, bool]:
        code = str(exc)
        if code.startswith("migration_") and code.replace("_", "").isalnum():
            return code, code not in {
                "migration_destination_collision",
                "migration_destination_identity_changed",
                "migration_source_hash_mismatch",
                "migration_source_identity_changed",
            }
        if isinstance(exc, FileNotFoundError):
            return "migration_source_missing", True
        return "migration_storage_unavailable", True

    def advance(self, run_id: str, *, batch_size: int = 16) -> dict[str, object]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with _lock:
            try:
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    if run.state not in {"copying", "ready"}:
                        raise ValueError("migration_not_copying")
                    source = build_configured_backend(
                        parse_provider_config(json.loads(run.source_config))
                    )
                    candidate = build_configured_backend(
                        parse_provider_config(json.loads(run.destination_config))
                    )
                    if (
                        namespace_ref(source) != run.source_provider_ref
                        or namespace_ref(candidate) != run.destination_provider_ref
                    ):
                        raise ValueError("migration_destination_identity_changed")
                    with allow_retained_destination_destruction(
                        source=source, destination=candidate
                    ):
                        candidate.ensure_setup()
                    policy = MigrationPolicy.model_validate_json(run.policy_json)
                    _retain(run_id)
                    pending = [
                        obj.id
                        for obj in _objects(session, run_id)
                        if obj.in_final and obj.state not in {"verified", "skipped"}
                    ][: max(1, min(batch_size, 64))]
                self._reserve(run_id, candidate)
                # Default one transfer. Explicit concurrency is bounded; each
                # transfer owns its session and exact source/destination proof.
                for offset in range(0, len(pending), policy.concurrency):
                    with self.factory.scoped_session() as session:
                        if self._run(session, run_id).state != "copying":
                            break
                    self._reserve(run_id, candidate)
                    ids = pending[offset : offset + policy.concurrency]
                    if policy.concurrency == 1:
                        assert ids[0] is not None
                        self._copy(run_id, ids[0], source, candidate)
                    else:
                        with ThreadPoolExecutor(max_workers=policy.concurrency) as pool:
                            futures = [
                                pool.submit(
                                    self._copy, run_id, value, source, candidate
                                )
                                for value in ids
                            ]
                            for future in as_completed(futures):
                                future.result()
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    if run.state == "copying" and all(
                        obj.state in {"verified", "skipped"}
                        for obj in _objects(session, run_id)
                        if obj.in_final
                    ):
                        transition(session, run, "ready")
                    session.commit()
            except Exception as exc:
                code, retryable = self._safe_failure(exc)
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    if run.state in {"copying", "ready"}:
                        transition(
                            session, run, "paused", error=code, retryable=retryable
                        )
                        session.commit()
                raise
        return self.get(run_id)

    def work_pending(self) -> None:
        with self.factory.scoped_session() as session:
            ids = [
                run.id
                for run in session.exec(select(VaultMigrationRun)).all()
                if run.state == "copying"
            ]
        for run_id in ids:
            self.advance(run_id, batch_size=4)

    def _copy(
        self,
        run_id: str,
        object_id: int,
        source: StorageBackend,
        candidate: StorageBackend,
    ) -> None:
        try:
            self._copy_object(run_id, object_id, source, candidate)
        except Exception as exc:
            code, retryable = self._safe_failure(exc)
            with self.factory.scoped_session() as session:
                obj = session.get(VaultMigrationObject, object_id)
                if obj is not None:
                    obj.state, obj.error_code, obj.retryable = "failed", code, retryable
                    session.add(obj)
                    session.commit()
            raise

    def _copy_object(
        self,
        run_id: str,
        object_id: int,
        source: StorageBackend,
        candidate: StorageBackend,
    ) -> None:
        from app.modules.storage.migration_progress import (
            COPIED_BYTES,
            COPIED_OBJECTS,
            RETRIED_OBJECTS,
        )
        from app.modules.storage.migration_transfer import ChunkReader, ThrottledReader

        with self.factory.scoped_session() as session:
            obj = session.get(VaultMigrationObject, object_id)
            run = self._run(session, run_id)
            assert obj is not None
            provider = str(json.loads(run.destination_summary_json)["provider"])
            if obj.source_receipt and not source.creation_matches(
                CreationReceipt(**json.loads(obj.source_receipt))
            ):
                raise ValueError("migration_source_identity_changed")
            if _hash(source, obj.source_key) != (obj.size_bytes, obj.sha256):
                raise ValueError("migration_source_hash_mismatch")
            if obj.destination_receipt:
                receipt = CreationReceipt(**json.loads(obj.destination_receipt))
                if not candidate.creation_matches(receipt):
                    raise ValueError("migration_destination_identity_changed")
                if _hash(candidate, obj.destination_key) == (
                    obj.size_bytes,
                    obj.sha256,
                ):
                    obj.state, obj.verified_at = "skipped", utcnow()
                    obj.error_code = None
                    run.last_activity_at = utcnow()
                    session.add(obj)
                    session.add(run)
                    session.commit()
                    return
                with allow_retained_destination_destruction(
                    source=source, destination=candidate
                ):
                    if not candidate.rollback_create(receipt):
                        raise ValueError("migration_destination_identity_changed")
                obj.destination_receipt = None
                session.add(obj)
                session.commit()
            elif candidate.exists(obj.destination_key):
                raise ValueError("migration_destination_collision")
            obj.attempts += 1
            if obj.attempts > 1:
                RETRIED_OBJECTS.labels(provider).inc()
            obj.state = "copying"
            session.add(obj)
            session.commit()
            policy = MigrationPolicy.model_validate_json(run.policy_json)
            limit = policy.bandwidth_bytes_per_second
            if limit:
                limit = max(1, limit // policy.concurrency)

            def checkpoint() -> None:
                with self.factory.scoped_session() as progress_session:
                    state = self._run(progress_session, run_id).state
                    if state not in {"copying", "delta_copy"}:
                        raise ValueError("migration_copy_paused")

            # Hash during the streamed transfer, before either adapter can
            # publish. Only the destination adapter may need a bounded spool.
            with ChunkReader(source.stream_chunks(obj.source_key)) as stream:
                migration_journal.append(
                    {
                        "phase": "copy_intent",
                        "run_id": run_id,
                        "nonce": run.journal_nonce,
                        "object_id": obj.id,
                    }
                )
                receipt = candidate.create_stream(
                    ThrottledReader(
                        stream,
                        limit,
                        expected_size=obj.size_bytes,
                        expected_sha256=obj.sha256,
                        checkpoint=checkpoint,
                    ),
                    obj.destination_key,
                )
                migration_journal.append(
                    {
                        "phase": "copy_receipt",
                        "run_id": run_id,
                        "nonce": run.journal_nonce,
                        "object_id": obj.id,
                        "receipt": asdict(receipt),
                    }
                )
                obj.destination_receipt = _json(asdict(receipt))
                obj.state = "copied"
                session.add(obj)
                session.commit()
            if not candidate.creation_matches(receipt) or _hash(
                candidate, obj.destination_key
            ) != (obj.size_bytes, obj.sha256):
                raise ValueError("migration_destination_verification_failed")
            obj.state, obj.verified_at = "verified", utcnow()
            obj.error_code, obj.retryable = None, False
            run.last_activity_at = utcnow()
            session.add(obj)
            session.add(run)
            session.commit()
            COPIED_BYTES.labels(provider).inc(obj.size_bytes)
            COPIED_OBJECTS.labels(provider).inc()

    def _phase(self, run_id: str, state: str, **kwargs) -> None:
        with self.factory.scoped_session() as session:
            transition(session, self._run(session, run_id), state, **kwargs)
            session.commit()

    def cutover(self, run_id: str) -> dict[str, object]:
        with _lock:
            with self.factory.scoped_session() as session:
                run = self._run(session, run_id)
                if run.state not in {"copying", "ready", "paused"}:
                    raise ValueError("migration_not_ready")
                source = build_configured_backend(
                    parse_provider_config(json.loads(run.source_config))
                )
                candidate_config = parse_provider_config(
                    json.loads(run.destination_config)
                )
                candidate = build_configured_backend(candidate_config)
                with allow_retained_destination_destruction(
                    source=source, destination=candidate
                ):
                    candidate.ensure_setup()
                nonce = run.journal_nonce
                transition(session, run, "cutover_pending")
                session.commit()
            committed = False
            try:
                migration_journal.append(
                    {"phase": "cutover_intent", "run_id": run_id, "nonce": nonce}
                )
                self._phase(run_id, "draining")
                begin_restore_maintenance()
                self._phase(run_id, "delta_copy")
                # Read admission remains open throughout the final census/copy.
                # Only the atomic catalogue switch needs an exclusive planning gate.
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    if _digest(_source_config(session)) != run.source_digest:
                        raise ValueError("migration_source_generation_changed")
                    self._census(session, run, source, candidate, final=True)
                    session.commit()
                    ids = [obj.id for obj in _objects(session, run_id) if obj.in_final]
                self._reserve(run_id, candidate)
                for object_id in ids:
                    assert object_id is not None
                    self._reserve(run_id, candidate)
                    self._copy(run_id, object_id, source, candidate)
                self._phase(run_id, "verifying")
                with self.factory.scoped_session() as session:
                    run = self._run(session, run_id)
                    objects = [obj for obj in _objects(session, run_id) if obj.in_final]
                    if any(obj.state not in {"verified", "skipped"} for obj in objects):
                        raise ValueError("migration_partial_destination")
                    _backup(
                        run.backup_id,
                        run.backup_source_ref,
                        expected_digest=json.loads(run.backup_summary_json)[
                            "archive_sha256"
                        ],
                    )
                    manifest = _digest(
                        _json(
                            [
                                (
                                    obj.source_key,
                                    obj.destination_key,
                                    obj.size_bytes,
                                    obj.sha256,
                                )
                                for obj in sorted(
                                    objects, key=lambda value: value.source_key
                                )
                            ]
                        )
                    )
                self._phase(run_id, "activating")
                with generations.activation():
                    with self.factory.scoped_session() as session:
                        run = self._run(session, run_id)
                        objects = [
                            obj for obj in _objects(session, run_id) if obj.in_final
                        ]
                        migration_journal.append(
                            {
                                "phase": "activation_intent",
                                "run_id": run_id,
                                "nonce": nonce,
                                "source_epoch": run.source_epoch,
                                "destination_epoch": run.destination_epoch,
                                "manifest_sha256": manifest,
                            }
                        )
                        self._remap(session, objects)
                        from app.modules.storage.migration_uploads import (
                            activate_uploads,
                        )

                        activate_uploads(
                            session, objects, source=source, destination=candidate
                        )
                        for obj in objects:
                            assert obj.destination_receipt is not None
                            record_creation(
                                session,
                                CreationReceipt(**json.loads(obj.destination_receipt)),
                                object_kind=obj.resource_type,
                                sha256=obj.sha256,
                            )
                        with activating_storage_configuration():
                            config = update_storage_provider(
                                session,
                                provider=candidate_config.provider,
                                raw_config=candidate_config.model_dump(mode="json"),
                                commit=False,
                                apply_runtime=False,
                            )
                        generation = (
                            session.get(VaultGeneration, 1) or VaultGeneration()
                        )
                        generation.epoch, generation.activation_run_id = (
                            run.destination_epoch,
                            run.id,
                        )
                        generation.manifest_sha256, generation.first_write_at = (
                            manifest,
                            None,
                        )
                        run.manifest_sha256, run.activated_at = manifest, utcnow()
                        run.cleanup_after = utcnow() + timedelta(
                            days=MigrationPolicy.model_validate_json(
                                run.policy_json
                            ).retention_days
                        )
                        transition(session, run, "active")
                        session.add(generation)
                        session.commit()
                        committed = True
                        migration_journal.append(
                            {
                                "phase": "database_active",
                                "run_id": run_id,
                                "nonce": nonce,
                                "destination_epoch": run.destination_epoch,
                                "manifest_sha256": manifest,
                            }
                        )
                        activate_config(config)
                        generations.publish(candidate, run.destination_epoch)
                if not candidate.health_probe().get("ok"):
                    raise ValueError("migration_activated_destination_unhealthy")
                # The destination manifest is already byte-verified and its
                # activation committed. The normal audit owns a registered
                # mutation, so run it after releasing cutover maintenance.
                end_restore_maintenance()
                audit_id = self._run_audit(run.actor_id)
                with self.factory.scoped_session() as session:
                    row = self._run(session, run_id)
                    row.post_audit_id = audit_id
                    session.add(row)
                    session.commit()
                migration_journal.append(
                    {"phase": "complete", "run_id": run_id, "nonce": nonce}
                )
                _release(run_id)
                self.capacity.release("vault-migration:" + run_id)
                end_restore_maintenance()
            except Exception as exc:
                # A controlled failure may resume the source only after proving
                # that activation did not commit. Ambiguity keeps writes fenced.
                with self.factory.scoped_session() as session:
                    row = self._run(session, run_id)
                    generation = session.get(VaultGeneration, 1)
                    source_proven = (
                        not committed
                        and _digest(_source_config(session)) == row.source_digest
                        and (generation is None or generation.epoch == row.source_epoch)
                    )
                if source_proven and source.health_probe().get("ok"):
                    with generations.activation():
                        generations.publish(source, row.source_epoch)
                    code, retryable = self._safe_failure(exc)
                    self._phase(run_id, "paused", error=code, retryable=retryable)
                    migration_journal.append(
                        {"phase": "paused", "run_id": run_id, "nonce": nonce}
                    )
                    end_restore_maintenance()
                else:
                    hold_restore_maintenance()
                    self._phase(
                        run_id, "recovery_required", error="migration_recovery_required"
                    )
                raise
        return self.get(run_id)

    @staticmethod
    def _remap(session: Session, objects: list[VaultMigrationObject]) -> None:
        mapping = {obj.source_key: obj.destination_key for obj in objects}
        receipts = {obj.source_key: obj.destination_receipt for obj in objects}
        for model, attributes in (
            (File, ("path", "thumbnail_path")),
            (Model, ("thumbnail_path",)),
            (ThumbnailGeneration, ("storage_key",)),
            (ModelSourceCover, ("storage_key",)),
            (CaptureUploadSlot, ("storage_key",)),
            (StagingLease, ("destination_key",)),
        ):
            for row in session.exec(select(model)).all():
                for attribute in attributes:
                    if (
                        isinstance(row, File)
                        and attribute == "path"
                        and row.is_external
                    ):
                        continue
                    old = getattr(row, attribute)
                    if old in mapping:
                        setattr(row, attribute, mapping[old])
                        if model in {CaptureUploadSlot, StagingLease}:
                            row.receipt_json = receipts[old]
                        session.add(row)

    @staticmethod
    def _audit(session: Session, run: VaultMigrationRun, action: str) -> None:
        from app.db.models import AuditLog

        session.add(
            AuditLog(
                actor_id=run.actor_id,
                action=action,
                resource_type="vault_migration",
                diff_json=_json({"run_id": run.id, "state": run.state}),
            )
        )

    def recover(self, run_id: str) -> dict[str, object]:
        """Prove the committed epoch, or resume source-authoritative copy.

        Missing or conflicting evidence remains in maintenance. A destination
        epoch is never rolled back automatically, including before first write.
        """
        with _lock, _drained_recovery(), generations.activation():
            activated = False
            evidence = [
                event
                for event in migration_journal.events()
                if event.get("run_id") == run_id
            ]
            with self.factory.scoped_session() as session:
                run = self._run(session, run_id)
                if not evidence or any(
                    event.get("nonce") != run.journal_nonce for event in evidence
                ):
                    raise ValueError("migration_recovery_ambiguous")
                actor_id, nonce = run.actor_id, run.journal_nonce
                generation = session.get(VaultGeneration, 1)
                current = _digest(_source_config(session))
                active_intent = next(
                    (
                        event
                        for event in reversed(evidence)
                        if event["phase"] == "activation_intent"
                    ),
                    None,
                )
                if generation and generation.epoch == run.destination_epoch:
                    if (
                        not active_intent
                        or generation.activation_run_id != run.id
                        or generation.manifest_sha256
                        != active_intent.get("manifest_sha256")
                        or current != _digest(run.destination_config)
                    ):
                        raise ValueError("migration_recovery_ambiguous")
                    candidate = build_configured_backend(
                        parse_provider_config(json.loads(run.destination_config))
                    )
                    candidate.ensure_setup()
                    # Once destination mutations have been admitted, its live
                    # catalogue supersedes the activation snapshot: a later
                    # legitimate purge must not make recovery impossible.
                    snapshot = (
                        _objects(session, run_id)
                        if generation.first_write_at is None
                        else []
                    )
                    for obj in snapshot:
                        if not obj.in_final:
                            continue
                        if not obj.destination_receipt:
                            raise ValueError("migration_recovery_ambiguous")
                        receipt = CreationReceipt(**json.loads(obj.destination_receipt))
                        if not candidate.creation_matches(receipt) or _hash(
                            candidate, obj.destination_key
                        ) != (obj.size_bytes, obj.sha256):
                            raise ValueError("migration_recovery_destination_changed")
                    if not candidate.health_probe().get("ok"):
                        raise ValueError("migration_activated_destination_unhealthy")
                    if generation.first_write_at is not None:
                        for blob in census(session, candidate, candidate):
                            size, digest = _hash(candidate, blob.source_key)
                            if (
                                blob.expected_size is not None
                                and size != blob.expected_size
                            ) or (
                                blob.expected_sha256 is not None
                                and digest != blob.expected_sha256
                            ):
                                raise ValueError(
                                    "migration_recovery_destination_changed"
                                )
                    generations.publish(candidate, generation.epoch)
                    config = session.get(SystemConfig, 1)
                    if config is not None:
                        activate_config(config)
                    if run.state != "active":
                        transition(session, run, "recovery_required")
                        transition(session, run, "active")
                    session.add(run)
                    session.commit()
                    activated = True
                elif current == run.source_digest and (
                    generation is None or generation.epoch == run.source_epoch
                ):
                    # Recover only receipts actually recorded after create. An
                    # intent with bytes but no receipt is an unresolved collision.
                    for event in evidence:
                        if event["phase"] == "copy_receipt":
                            obj = session.get(
                                VaultMigrationObject, event.get("object_id")
                            )
                            if (
                                obj
                                and obj.run_id == run.id
                                and not obj.destination_receipt
                            ):
                                obj.destination_receipt = _json(event["receipt"])
                                obj.state = "copied"
                                session.add(obj)
                    _retain(run_id)
                    if run.state != "paused":
                        transition(session, run, "recovery_required")
                        transition(session, run, "paused", retryable=True)
                    run.error_code = None
                    session.add(run)
                    session.commit()
                    source = build_configured_backend(
                        parse_provider_config(json.loads(run.source_config))
                    )
                    source.ensure_setup()
                    generations.publish(source, run.source_epoch)
                    migration_journal.append(
                        {
                            "phase": "paused",
                            "run_id": run_id,
                            "nonce": run.journal_nonce,
                        }
                    )
                else:
                    raise ValueError("migration_recovery_ambiguous")
            end_restore_maintenance()
            if activated:
                try:
                    audit_id = self._run_audit(actor_id)
                    with self.factory.scoped_session() as session:
                        row = self._run(session, run_id)
                        row.post_audit_id = audit_id
                        session.add(row)
                        session.commit()
                    migration_journal.append(
                        {"phase": "complete", "run_id": run_id, "nonce": nonce}
                    )
                    _release(run_id)
                    self.capacity.release("vault-migration:" + run_id)
                except Exception:
                    hold_restore_maintenance()
                    self._phase(
                        run_id, "recovery_required", error="migration_recovery_required"
                    )
                    raise
        return self.get(run_id)

    def full_audit(self, run_id: str) -> dict[str, object]:
        with _lock:
            with self.factory.scoped_session() as session:
                run = self._run(session, run_id)
                if (
                    run.state != "active"
                    or generations.current_epoch() != run.destination_epoch
                ):
                    raise ValueError("migration_destination_not_active")
                actor_id = run.actor_id
            audit_id = self._run_audit(actor_id, full=True)
            with self.factory.scoped_session() as session:
                run = self._run(session, run_id)
                run.full_audit_id = audit_id
                session.add(run)
                self._audit(session, run, "vault.migration.full_audit")
                session.commit()
        return self.get(run_id)

    def retain(
        self, run_id: str, *, remove_credentials: bool = False
    ) -> dict[str, object]:
        with _lock, self.factory.scoped_session() as session:
            run = self._run(session, run_id)
            if run.state != "active":
                raise ValueError("migration_destination_not_active")
            run.cleanup_outcome = (
                "manual_cleanup" if remove_credentials else "retained_indefinitely"
            )
            if remove_credentials:
                run.source_config = "{}"
            run.cleanup_after = None
            transition(session, run, "complete")
            session.commit()
        return self.get(run_id)

    def cleanup(
        self,
        run_id: str,
        *,
        confirmation: str,
        source: bool,
        backup_id: str | None = None,
        backup_source_ref: str | None = None,
    ) -> dict[str, object]:
        with _lock:
            if confirmation != run_id:
                raise ValueError("migration_cleanup_confirmation_required")
            with self.factory.scoped_session() as session:
                run = self._run(session, run_id)
                if source:
                    if (
                        run.state != "active"
                        or generations.current_epoch() != run.destination_epoch
                        or run.cleanup_after is None
                        or ensure_utc(run.cleanup_after) > utcnow()
                    ):
                        raise ValueError("migration_source_grace_required")
                    if generations.has_readers(run.source_epoch):
                        raise ValueError("migration_source_readers_busy")
                    pending_uploads = session.exec(
                        select(ArtifactUploadSession).where(
                            ArtifactUploadSession.destination_ref
                            == run.source_provider_ref,
                            ArtifactUploadSession.protected_native_id.is_not(None),
                            ArtifactUploadSession.state.not_in(
                                ["completed", "aborted", "expired"]
                            ),
                        )
                    ).first()
                    if pending_uploads is not None:
                        raise ValueError("migration_source_upload_cleanup_required")
                    from app.modules.storage.migration_progress import audit_projection

                    audit = audit_projection(session, run.full_audit_id)
                    if (
                        not audit
                        or audit["state"] != "completed"
                        or audit["critical_count"]
                    ):
                        raise ValueError("migration_full_audit_required")
                    _backup(
                        backup_id or "",
                        backup_source_ref,
                        created_after=run.activated_at,
                    )
                    config = run.source_config
                else:
                    if run.state not in {
                        "planned",
                        "copying",
                        "ready",
                        "paused",
                        "failed",
                    }:
                        raise ValueError("migration_destination_discard_forbidden")
                    config = run.destination_config
                backend = build_configured_backend(
                    parse_provider_config(json.loads(config))
                )
                findings = []
                for obj in _objects(session, run_id):
                    if source and not obj.in_final:
                        continue
                    raw = obj.source_receipt if source else obj.destination_receipt
                    if raw is None:
                        key = obj.source_key if source else obj.destination_key
                        if not backend.exists(key):
                            continue
                        findings.append(
                            {"object_id": obj.id, "code": "ownership_unverified"}
                        )
                        continue
                    receipt = CreationReceipt(**json.loads(raw))
                    # A changed inode/version/hash is retained. Receipt proof is
                    # mandatory; key existence never authorizes cleanup.
                    if not backend.creation_matches(receipt):
                        if backend.exists(receipt.key):
                            findings.append(
                                {"object_id": obj.id, "code": "identity_changed"}
                            )
                        continue
                    if source and _hash(backend, receipt.key) != (
                        obj.size_bytes,
                        obj.sha256,
                    ):
                        findings.append(
                            {"object_id": obj.id, "code": "content_changed"}
                        )
                        continue
                    with allow_retained_destination_destruction(
                        source=get_bound_backend(), destination=backend
                    ):
                        if not backend.rollback_create(receipt):
                            findings.append(
                                {"object_id": obj.id, "code": "identity_changed"}
                            )
                        else:
                            from app.db.models import AuditLog

                            session.add(
                                AuditLog(
                                    actor_id=run.actor_id,
                                    action="vault.migration.object_deleted",
                                    resource_type="vault_migration",
                                    diff_json=_json(
                                        {
                                            "run_id": run.id,
                                            "object_id": obj.id,
                                            "side": "source"
                                            if source
                                            else "destination",
                                        }
                                    ),
                                )
                            )
                run.cleanup_findings = _json(findings)
                if not findings:
                    transition(session, run, "cleaned" if source else "discarded")
                    run.cleanup_outcome = "cleaned" if source else None
                session.add(run)
                self._audit(
                    session,
                    run,
                    "vault.migration.source_cleanup"
                    if source
                    else "vault.migration.discard",
                )
                session.commit()
                if not source and not findings:
                    _release(run_id)
                    migration_journal.append(
                        {
                            "phase": "discarded",
                            "run_id": run_id,
                            "nonce": run.journal_nonce,
                        }
                    )
                    self.capacity.release("vault-migration:" + run_id)
        return self.get(run_id)


def record_first_destination_write() -> None:
    """Record the no-rollback point before admitting any destination mutation."""
    epoch = generations.current_epoch()
    if epoch == "0":
        return
    from sqlalchemy import update

    from app.db.session import get_session_factory

    with get_session_factory().scoped_session() as session:
        row = session.get(VaultGeneration, 1)
        if row is None or row.epoch != epoch or row.first_write_at is not None:
            return
        result = session.execute(
            update(VaultGeneration)
            .where(
                VaultGeneration.id == 1,
                VaultGeneration.epoch == epoch,
                VaultGeneration.first_write_at.is_(None),
            )
            .values(first_write_at=utcnow())
        )
        session.commit()
        if result.rowcount:
            run = session.get(VaultMigrationRun, row.activation_run_id)
            if run is None:
                raise ValueError("migration_recovery_ambiguous")
            migration_journal.append(
                {
                    "phase": "first_write",
                    "run_id": row.activation_run_id,
                    "nonce": run.journal_nonce,
                    "destination_epoch": epoch,
                }
            )
