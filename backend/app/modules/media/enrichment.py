"""One bounded enrichment unit over durable metadata and preview generations.

The two outputs have independent claims and failure states. When both are due,
this processor shares one materialization, mesh load and compute permit. It never
owns source persistence or makes import success depend on a derivative.
"""

from __future__ import annotations

import tempfile
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Callable

from sqlmodel import col, or_, select

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    File,
    FileType,
    Model,
    ThumbnailGeneration,
    ThumbnailGenerationState,
)
from app.db.scopes import live
from app.db.session import SessionFactory
from app.modules.ingestion.extensions import after_commit
from app.modules.media import analysis_generations as analysis
from app.modules.media import compute_slots, enrichment_leases, gcode_parser, thumbnail
from app.modules.media.thumbnail_engine import (
    ThumbnailEngine,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.thumbnail_generations import (
    ThumbnailClaim,
    ThumbnailRenderer,
    claim_thumbnail,
    defer_thumbnail,
    finish_thumbnail,
    recipe_fingerprint,
)
from app.modules.printing.profile_detection import upsert_detected_profiles
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.storage_backend.contracts import StorageBackend

logger = get_logger(__name__)


class EnrichmentProcessor:
    def __init__(
        self,
        sessions: SessionFactory,
        backend: StorageBackend,
        *,
        engine: ThumbnailRenderer | None = None,
        retain_storage: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        self.sessions = sessions
        self.backend = backend
        self.engine = engine or ThumbnailEngine()
        self.retain_storage = retain_storage

    def work_one(self) -> bool:
        """Return whether an eligible unit was attempted; idle work is cheap."""
        with self.retain_storage():
            return self._work_one()

    def _work_one(self) -> bool:
        with self.sessions.scoped_session() as session:
            metadata_claim = analysis.claim_next(session)
            if metadata_claim is not None:
                file = session.get(File, metadata_claim.file_id)
            else:
                file = session.exec(
                    select(File)
                    .join(Model, Model.id == File.model_id)
                    .join(ThumbnailGeneration, ThumbnailGeneration.file_id == File.id)
                    .where(
                        live(File),
                        live(Model),
                        ThumbnailGeneration.source_sha256 == File.sha256,
                        ThumbnailGeneration.recipe_fingerprint == recipe_fingerprint(),
                        ThumbnailGeneration.processing_policy == "background",
                        col(ThumbnailGeneration.state).in_(
                            (
                                ThumbnailGenerationState.PENDING.value,
                                ThumbnailGenerationState.RUNNING.value,
                            )
                        ),
                        or_(
                            col(ThumbnailGeneration.lease_expires_at).is_(None),
                            ThumbnailGeneration.lease_expires_at <= utcnow(),
                        ),
                    )
                    .order_by(ThumbnailGeneration.created_at, ThumbnailGeneration.id)
                    .limit(1)
                ).first()
            if file is None:
                return False
            preview_claim = claim_thumbnail(session, file)
            if metadata_claim is None and preview_claim is None:
                return False
            token = metadata_claim.token if metadata_claim else preview_claim.token
            slot = compute_slots.acquire(
                session, token, lease_seconds=analysis.LEASE_SECONDS
            )
            if slot is None:
                self._defer(metadata_claim, preview_claim, "compute_busy")
                return False
            slot_id = slot.id
            # Detach the immutable source snapshot; computation holds no SQL transaction.
            session.refresh(file)
            session.expunge(file)
            session.commit()
        try:
            allocation = CapacityResource.for_path(
                Path(tempfile.gettempdir()),
                file.size_bytes * 3 + 16 * 1024**2,
                role="artifact enrichment",
            )
            try:
                capacity = CapacityManager(self.sessions).reserve(
                    f"enrichment:{file.id}:{token}",
                    [
                        allocation,
                        CapacityResource.for_path(
                            settings.thumb_dir,
                            16 * 1024**2 if preview_claim else 0,
                            role="enrichment output",
                        ),
                    ],
                )
            except OperationError:
                self._defer(metadata_claim, preview_claim, "capacity_busy")
                return False
            try:
                with (
                    enrichment_leases.keep_alive(
                        self.sessions, slot_id, token, metadata_claim, preview_claim
                    ),
                    resolve(file, backend=self.backend).materialize(
                        capacity_claimed=True
                    ) as source,
                ):
                    result, metadata = self._compute(
                        source,
                        file,
                        metadata_claim is not None,
                        preview_claim is not None,
                    )
            finally:
                capacity.release()
            metadata_ok = True
            if metadata_claim is not None:
                with self.sessions.scoped_session() as session:
                    if (
                        file.file_type != FileType.GCODE
                        and not any(value is not None for value in metadata.values())
                        and not metadata_claim.preserve_metadata
                    ):
                        analysis.fail_analysis(
                            session,
                            metadata_claim,
                            (
                                result.failure_reason
                                or ThumbnailFailureReason.NO_GEOMETRY
                            ).value,
                            retryable=False,
                        )
                        metadata_ok = False
                    else:
                        metadata_ok = analysis.publish_metadata(
                            session, metadata_claim, metadata
                        )
            if preview_claim is not None:
                with self.sessions.scoped_session() as session:
                    current = session.get(File, file.id)
                    if current is not None:
                        finish_thumbnail(
                            session,
                            current,
                            preview_claim,
                            result,
                            backend=self.backend,
                        )
            if metadata_claim is not None and metadata_ok:
                # Profiles and optional similarity submission are replayable derived
                # operations. Only mark the metadata unit finished after their handoff.
                with self.sessions.scoped_session() as session:
                    upsert_detected_profiles(session, metadata)
                after_commit(self.sessions, file.id, metadata_claim.actor_user_id, None)
                with self.sessions.scoped_session() as session:
                    analysis.finish_analysis(session, metadata_claim)
            return True
        except ArtifactContentError:
            self._fail(metadata_claim, preview_claim, "source_unavailable")
            return True
        except OperationError as exc:
            if exc.kind is ErrorKind.BUSY:
                self._defer(metadata_claim, preview_claim, "storage_busy")
            else:
                self._fail(metadata_claim, preview_claim, "enrichment_failed")
            return True
        except Exception:
            logger.exception("Artifact enrichment failed", extra={"file_id": file.id})
            self._fail(metadata_claim, preview_claim, "enrichment_failed")
            return True
        finally:
            with self.sessions.scoped_session() as session:
                compute_slots.release(session, slot_id, token)
                session.commit()

    def _compute(
        self,
        source: Path,
        file: File,
        metadata_requested: bool,
        preview_requested: bool,
    ):
        if file.file_type == FileType.GCODE:
            metadata = gcode_parser.parse(source) if metadata_requested else {}
            image = thumbnail.extract(source) if preview_requested else None
            result = ThumbnailResult(
                image=image,
                geometry={},
                strategy=ThumbnailStrategy.EMBEDDED
                if image
                else ThumbnailStrategy.NONE,
                complete=True,
                failure_reason=None
                if image
                else ThumbnailFailureReason.NO_EMBEDDED_THUMBNAIL,
                duration_ms=0,
                peak_rss_bytes=None,
            )
            return result, metadata
        result = self.engine.generate(
            ThumbnailRequest(
                path=source,
                file_type=file.file_type.value,
                include_geometry=metadata_requested,
                include_thumbnail=preview_requested,
                output_format="WEBP",
                reason="background_enrichment",
            )
        )
        return result, result.geometry

    def _defer(
        self,
        metadata: analysis.AnalysisClaim | None,
        preview: ThumbnailClaim | None,
        reason: str,
    ) -> None:
        with self.sessions.scoped_session() as session:
            if metadata:
                analysis.defer_analysis(session, metadata, reason)
            if preview:
                defer_thumbnail(session, preview, reason=reason)
                session.commit()

    def _fail(
        self,
        metadata: analysis.AnalysisClaim | None,
        preview: ThumbnailClaim | None,
        reason: str,
    ) -> None:
        with self.sessions.scoped_session() as session:
            if metadata:
                analysis.fail_analysis(session, metadata, reason)
            if preview:
                # The generation's failure policy owns bounded retries. Supply a
                # source/storage failure without manufacturing renderer output.
                file = session.get(File, metadata.file_id) if metadata else None
                if file is None:
                    generation = session.get(ThumbnailGeneration, preview.generation_id)
                    file = session.get(File, generation.file_id) if generation else None
                if file is not None:
                    finish_thumbnail(
                        session,
                        file,
                        preview,
                        ThumbnailResult(
                            image=None,
                            geometry={},
                            strategy=ThumbnailStrategy.NONE,
                            complete=False,
                            failure_reason=ThumbnailFailureReason.STORAGE,
                            duration_ms=0,
                            peak_rss_bytes=None,
                        ),
                        backend=self.backend,
                    )
