"""Durable cache and fleet-wide coordination for thumbnail derivatives."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Callable, TypeVar

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, or_, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    File,
    Model,
    ThumbnailGeneration,
    ThumbnailGenerationState,
    ThumbnailRenderSlot,
)
from app.services import thumbnail
from app.services.artifact_content import ArtifactContentError, resolve
from app.services.storage_backend import (
    StorageBackend,
    StorageCollisionError,
    get_backend,
)
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    publish_bytes,
    replace_owned_bytes,
)
from app.services.thumbnail_engine import (
    ThumbnailEngine,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)

logger = get_logger(__name__)
_T = TypeVar("_T")
_SQLITE_LOCK_RETRIES = 8


def _retry_sqlite_lock(session: Session, operation: Callable[[], _T]) -> _T:
    for attempt in range(_SQLITE_LOCK_RETRIES):
        try:
            return operation()
        except OperationalError as exc:
            if (
                session.get_bind().dialect.name != "sqlite"
                or "locked" not in str(exc).lower()
            ):
                raise
            session.rollback()
            if attempt + 1 == _SQLITE_LOCK_RETRIES:
                raise
            time.sleep(min(0.005 * 2**attempt, 0.1))
    raise AssertionError("unreachable")


class ThumbnailEnsureOutcome(str, Enum):
    GENERATED = "generated"
    CACHED = "cached"
    COALESCED = "coalesced"
    NEGATIVE_CACHED = "negative_cached"
    FAILED = "failed"


@dataclass(frozen=True)
class ThumbnailEnsureResult:
    outcome: ThumbnailEnsureOutcome
    generation_id: int | None
    strategy: str | None = None
    failure_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.outcome in (
            ThumbnailEnsureOutcome.GENERATED,
            ThumbnailEnsureOutcome.CACHED,
        )


_DETERMINISTIC_FAILURES = {
    ThumbnailFailureReason.INVALID_SOURCE.value,
    ThumbnailFailureReason.UNSUPPORTED_FORMAT.value,
    ThumbnailFailureReason.NO_GEOMETRY.value,
    ThumbnailFailureReason.RESOURCE_LIMIT.value,
    ThumbnailFailureReason.RENDERER_NO_OUTPUT.value,
}


def recipe_fingerprint() -> str:
    return f"{PREVIEW_PROFILE.recipe_fingerprint}-w{settings.model_thumbnail_width}"


def _generation_query(file_row: File):
    assert file_row.id is not None
    return select(ThumbnailGeneration).where(
        ThumbnailGeneration.file_id == file_row.id,
        ThumbnailGeneration.source_sha256 == file_row.sha256,
        ThumbnailGeneration.recipe_fingerprint == recipe_fingerprint(),
    )


def _get_or_create_generation(session: Session, file_row: File) -> ThumbnailGeneration:
    def operation() -> ThumbnailGeneration:
        row = session.exec(_generation_query(file_row)).first()
        if row is not None:
            return row
        assert file_row.id is not None
        row = ThumbnailGeneration(
            file_id=file_row.id,
            source_sha256=file_row.sha256,
            recipe_fingerprint=recipe_fingerprint(),
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            winner = session.exec(_generation_query(file_row)).first()
            if winner is None:
                raise
            return winner
        session.refresh(row)
        return row

    return _retry_sqlite_lock(session, operation)


def _cache_is_valid(row: ThumbnailGeneration, backend: StorageBackend) -> bool:
    if not row.storage_key or row.output_size_bytes is None:
        return False
    info = backend.object_info(row.storage_key)
    if info is None or info.size != row.output_size_bytes:
        return False
    return row.output_etag is None or info.etag is None or row.output_etag == info.etag


def _publish_pointers(
    session: Session,
    file_row: File,
    storage_key: str,
    *,
    promote: bool,
) -> None:
    file_row.thumbnail_path = storage_key
    session.add(file_row)
    if promote:
        model = session.get(Model, file_row.model_id)
        if model is not None:
            model.thumbnail_file_id = file_row.id
            model.thumbnail_path = storage_key
            session.add(model)


def _ensure_slots(session: Session) -> None:
    def operation() -> None:
        limit = max(int(settings.max_render_jobs), 1)
        existing = set(session.exec(select(ThumbnailRenderSlot.slot_number)).all())
        changed = False
        for slot_number in range(1, limit + 1):
            if slot_number not in existing:
                session.add(ThumbnailRenderSlot(slot_number=slot_number))
                changed = True
        if changed:
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

    _retry_sqlite_lock(session, operation)


def _acquire_slot(
    session: Session, generation: ThumbnailGeneration, token: str
) -> ThumbnailRenderSlot | None:
    _ensure_slots(session)
    now = utcnow()
    limit = max(int(settings.max_render_jobs), 1)
    generation_id = generation.id
    expires_at = now + timedelta(
        seconds=max(int(settings.mesh_stream_timeout_seconds) + 30, 60)
    )

    def operation() -> int | None:
        candidates = session.exec(
            select(ThumbnailRenderSlot)
            .where(
                ThumbnailRenderSlot.slot_number <= limit,  # type: ignore[operator]
                or_(
                    ThumbnailRenderSlot.lease_token.is_(None),  # type: ignore[union-attr]
                    ThumbnailRenderSlot.lease_expires_at < now,  # type: ignore[operator]
                ),
            )
            .order_by(ThumbnailRenderSlot.slot_number)  # type: ignore[arg-type]
            .with_for_update(skip_locked=True)
        ).all()
        for candidate in candidates:
            assert candidate.id is not None
            claimed = session.connection().execute(
                update(ThumbnailRenderSlot)
                .where(
                    ThumbnailRenderSlot.id == candidate.id,
                    or_(
                        ThumbnailRenderSlot.lease_token.is_(None),  # type: ignore[union-attr]
                        ThumbnailRenderSlot.lease_expires_at < now,  # type: ignore[operator]
                    ),
                )
                .values(
                    lease_token=token,
                    generation_id=generation_id,
                    lease_expires_at=expires_at,
                    updated_at=now,
                )
            )
            if claimed.rowcount == 1:
                session.commit()
                return candidate.id
            session.rollback()
        return None

    slot_id = _retry_sqlite_lock(session, operation)
    return session.get(ThumbnailRenderSlot, slot_id) if slot_id is not None else None


def _claim_generation(
    session: Session,
    generation: ThumbnailGeneration,
    *,
    token: str,
    now,
    force: bool,
) -> bool:
    """Atomically win one recipe even on SQLite, where row locks are ignored."""
    assert generation.id is not None
    claimable_states = [
        ThumbnailGenerationState.PENDING.value,
        ThumbnailGenerationState.RUNNING.value,
        ThumbnailGenerationState.READY.value,
    ]
    if force:
        claimable_states.append(ThumbnailGenerationState.FAILED.value)
    expires_at = now + timedelta(
        seconds=max(int(settings.mesh_stream_timeout_seconds) + 30, 60)
    )

    def claim() -> int:
        result = session.connection().execute(
            update(ThumbnailGeneration)
            .where(
                ThumbnailGeneration.id == generation.id,
                ThumbnailGeneration.state.in_(claimable_states),  # type: ignore[attr-defined]
                or_(
                    ThumbnailGeneration.state == ThumbnailGenerationState.READY.value,
                    ThumbnailGeneration.lease_expires_at.is_(None),  # type: ignore[union-attr]
                    ThumbnailGeneration.lease_expires_at <= now,  # type: ignore[operator]
                ),
            )
            .values(
                state=ThumbnailGenerationState.RUNNING.value,
                attempts=ThumbnailGeneration.attempts + 1,
                lease_token=token,
                lease_expires_at=expires_at,
                updated_at=now,
            )
        )
        session.commit()
        return result.rowcount

    if _retry_sqlite_lock(session, claim) != 1:
        return False
    _retry_sqlite_lock(session, lambda: session.refresh(generation))
    return generation.lease_token == token


def _lease_is_owned(
    session: Session, generation: ThumbnailGeneration, token: str
) -> bool:
    if not token:
        return True
    _retry_sqlite_lock(session, lambda: session.refresh(generation))
    return generation.lease_token == token


def _release_slot(session: Session, slot_id: int | None, token: str) -> None:
    if slot_id is None:
        return
    slot = _retry_sqlite_lock(
        session, lambda: session.get(ThumbnailRenderSlot, slot_id)
    )
    if slot is None or slot.lease_token != token:
        return
    slot.generation_id = None
    slot.lease_token = None
    slot.lease_expires_at = None
    slot.updated_at = utcnow()
    session.add(slot)


def _mark_failure(
    session: Session,
    generation: ThumbnailGeneration,
    reason: str,
    *,
    slot_id: int | None,
    token: str,
) -> ThumbnailEnsureResult:
    if not _lease_is_owned(session, generation, token):
        session.rollback()
        return ThumbnailEnsureResult(
            ThumbnailEnsureOutcome.FAILED,
            generation.id,
            failure_reason=ThumbnailFailureReason.LEASE_LOST.value,
        )
    deterministic = reason in _DETERMINISTIC_FAILURES
    generation.state = (
        ThumbnailGenerationState.FAILED
        if deterministic or generation.attempts >= 3
        else ThumbnailGenerationState.PENDING
    )
    generation.failure_reason = reason
    generation.lease_token = None
    generation.lease_expires_at = (
        None
        if generation.state == ThumbnailGenerationState.FAILED
        else utcnow() + timedelta(seconds=min(2**generation.attempts, 60))
    )
    generation.updated_at = utcnow()
    session.add(generation)
    _release_slot(session, slot_id, token)
    session.commit()
    return ThumbnailEnsureResult(
        ThumbnailEnsureOutcome.FAILED,
        generation.id,
        failure_reason=reason,
    )


def _publish_encoded(
    session: Session,
    backend: StorageBackend,
    generation: ThumbnailGeneration,
    file_row: File,
    encoded: bytes,
    result: ThumbnailResult,
    *,
    promote: bool,
    slot_id: int | None,
    token: str,
) -> ThumbnailEnsureResult:
    if not _lease_is_owned(session, generation, token):
        session.rollback()
        return ThumbnailEnsureResult(
            ThumbnailEnsureOutcome.FAILED,
            generation.id,
            failure_reason=ThumbnailFailureReason.LEASE_LOST.value,
        )
    assert file_row.id is not None
    key = backend.thumbnail_variant_key(
        file_row.id, file_row.sha256, generation.recipe_fingerprint
    )
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        receipt = publish_bytes(
            session,
            backend,
            key,
            encoded,
            object_kind="thumbnail",
            sha256=digest,
        )
        output_size = receipt.size
        output_etag = receipt.etag
    except StorageCollisionError:
        existing = backend.object_info(key)
        if (
            existing is None
            or existing.size != len(encoded)
            or hashlib.sha256(backend.read_bytes(key)).hexdigest() != digest
        ):
            try:
                receipt = replace_owned_bytes(
                    session,
                    backend,
                    key,
                    encoded,
                    object_kind="thumbnail",
                )
            except (UnsafeStorageDeleteError, NotImplementedError):
                return _mark_failure(
                    session,
                    generation,
                    ThumbnailFailureReason.STORAGE.value,
                    slot_id=slot_id,
                    token=token,
                )
            output_size = receipt.size
            output_etag = receipt.etag
        else:
            output_size = existing.size
            output_etag = existing.etag

    generation.state = ThumbnailGenerationState.READY
    generation.storage_key = key
    generation.output_sha256 = digest
    generation.output_size_bytes = output_size
    generation.output_etag = output_etag
    generation.width = int(settings.model_thumbnail_width)
    generation.height = round(int(settings.model_thumbnail_width) * 3 / 4)
    generation.strategy = result.strategy.value
    generation.complete = result.complete
    generation.failure_reason = None
    generation.duration_ms = result.duration_ms
    generation.peak_rss_bytes = result.peak_rss_bytes
    generation.lease_token = None
    generation.lease_expires_at = None
    generation.updated_at = utcnow()
    session.add(generation)
    _publish_pointers(session, file_row, key, promote=promote)
    _release_slot(session, slot_id, token)
    session.commit()
    return ThumbnailEnsureResult(
        ThumbnailEnsureOutcome.GENERATED,
        generation.id,
        strategy=result.strategy.value,
    )


def ensure_thumbnail(
    session: Session,
    file_row: File,
    *,
    force: bool = False,
    promote: bool = True,
    backend: StorageBackend | None = None,
    engine: ThumbnailEngine | None = None,
) -> ThumbnailEnsureResult:
    backend = backend or get_backend()
    generation = _get_or_create_generation(session, file_row)
    now = utcnow()

    if generation.state == ThumbnailGenerationState.READY and _cache_is_valid(
        generation, backend
    ):
        assert generation.storage_key is not None
        _publish_pointers(session, file_row, generation.storage_key, promote=promote)
        session.commit()
        return ThumbnailEnsureResult(
            ThumbnailEnsureOutcome.CACHED,
            generation.id,
            strategy=generation.strategy,
        )
    if not force and generation.state == ThumbnailGenerationState.FAILED:
        return ThumbnailEnsureResult(
            ThumbnailEnsureOutcome.NEGATIVE_CACHED,
            generation.id,
            failure_reason=generation.failure_reason,
        )
    if (
        generation.lease_token
        and generation.lease_expires_at is not None
        and ensure_utc(generation.lease_expires_at) > now
    ):
        return ThumbnailEnsureResult(ThumbnailEnsureOutcome.COALESCED, generation.id)
    if (
        generation.state == ThumbnailGenerationState.PENDING
        and generation.lease_expires_at is not None
        and ensure_utc(generation.lease_expires_at) > now
    ):
        return ThumbnailEnsureResult(ThumbnailEnsureOutcome.COALESCED, generation.id)

    token = secrets.token_hex(32)
    if not _claim_generation(session, generation, token=token, now=now, force=force):
        return ThumbnailEnsureResult(ThumbnailEnsureOutcome.COALESCED, generation.id)

    slot = _acquire_slot(session, generation, token)
    if slot is None:
        generation.state = ThumbnailGenerationState.PENDING
        generation.lease_token = None
        generation.lease_expires_at = now + timedelta(seconds=1)
        generation.updated_at = now
        session.add(generation)
        session.commit()
        return ThumbnailEnsureResult(ThumbnailEnsureOutcome.COALESCED, generation.id)

    try:
        with resolve(file_row, backend=backend).materialize() as source:
            rendered = (engine or ThumbnailEngine()).generate(
                ThumbnailRequest(
                    path=source,
                    file_type=file_row.file_type.value,
                    include_geometry=False,
                    reason="repair",
                    output_format="WEBP",
                )
            )
    except ArtifactContentError:
        return _mark_failure(
            session,
            generation,
            ThumbnailFailureReason.INVALID_SOURCE.value,
            slot_id=slot.id,
            token=token,
        )
    if rendered.image is None:
        return _mark_failure(
            session,
            generation,
            (
                rendered.failure_reason or ThumbnailFailureReason.RENDERER_NO_OUTPUT
            ).value,
            slot_id=slot.id,
            token=token,
        )
    try:
        encoded = thumbnail.to_webp(rendered.image)
    except ValueError:
        return _mark_failure(
            session,
            generation,
            ThumbnailFailureReason.INVALID_SOURCE.value,
            slot_id=slot.id,
            token=token,
        )
    try:
        return _publish_encoded(
            session,
            backend,
            generation,
            file_row,
            encoded,
            rendered,
            promote=promote,
            slot_id=slot.id,
            token=token,
        )
    except Exception:  # noqa: BLE001 - derivative failure is persisted, not fatal
        logger.exception(
            "thumbnail generation publication failed",
            extra={"file_id": file_row.id},
        )
        session.rollback()
        return _mark_failure(
            session,
            generation,
            ThumbnailFailureReason.STORAGE.value,
            slot_id=slot.id,
            token=token,
        )


def publish_precomputed_thumbnail(
    session: Session,
    file_row: File,
    data: bytes,
    *,
    strategy: ThumbnailStrategy,
    complete: bool,
    promote: bool,
    normalize: bool = True,
    backend: StorageBackend | None = None,
) -> ThumbnailEnsureResult:
    backend = backend or get_backend()
    generation = _get_or_create_generation(session, file_row)
    generation.state = ThumbnailGenerationState.RUNNING
    generation.attempts += 1
    generation.updated_at = utcnow()
    session.add(generation)
    session.commit()
    try:
        encoded = thumbnail.to_webp(data, normalize=normalize)
    except ValueError:
        return _mark_failure(
            session,
            generation,
            ThumbnailFailureReason.INVALID_SOURCE.value,
            slot_id=None,
            token="",
        )
    result = ThumbnailResult(
        image=data,
        geometry={
            "bbox_x_mm": None,
            "bbox_y_mm": None,
            "bbox_z_mm": None,
            "volume_mm3": None,
            "triangle_count": None,
        },
        strategy=strategy,
        complete=complete,
        failure_reason=None,
        duration_ms=0,
        peak_rss_bytes=None,
    )
    try:
        return _publish_encoded(
            session,
            backend,
            generation,
            file_row,
            encoded,
            result,
            promote=promote,
            slot_id=None,
            token="",
        )
    except Exception:  # noqa: BLE001 - Artifact remains valid without its derivative
        logger.exception(
            "precomputed thumbnail publication failed",
            extra={"file_id": file_row.id},
        )
        session.rollback()
        return _mark_failure(
            session,
            generation,
            ThumbnailFailureReason.STORAGE.value,
            slot_id=None,
            token="",
        )


__all__ = [
    "ThumbnailEnsureOutcome",
    "ThumbnailEnsureResult",
    "ensure_thumbnail",
    "publish_precomputed_thumbnail",
    "recipe_fingerprint",
]
