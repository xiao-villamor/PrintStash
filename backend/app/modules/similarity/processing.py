"""One durable unit of geometry work; runtime owns wakeups and maintenance drain."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, nullcontext

from printstash_core.mesh.similarity import GeometryError
from sqlmodel import Session, col, select

from app.core.errors import OperationError
from app.db.models import File, GeometryFingerprint, SimilarityRun, User
from app.db.session import SessionFactory
from app.modules.media import compute_slots, geometry_analysis
from app.modules.media.fingerprints import FingerprintResult
from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest
from app.modules.similarity import (
    candidates,
    fingerprints,
    retrieval,
    runs,
    verification_cache,
)
from app.modules.similarity.configuration import SimilaritySettings, read_settings
from app.modules.storage import artifact_content
from app.modules.storage.storage_backend.contracts import StorageBackend


class SimilarityProcessor:
    def __init__(
        self,
        sessions: SessionFactory,
        backend: StorageBackend,
        *,
        retain_storage: Callable[[], AbstractContextManager[None]] = nullcontext,
    ):
        self.sessions = sessions
        self.backend = backend
        self._retain_storage = retain_storage

    def work_one(self) -> bool:
        """A mesh, shortlist or pair, with the checkpoint committed before return."""
        with self.sessions.scoped_session() as session:
            enabled = read_settings(session).enabled
            if enabled:
                runs.schedule_due(session)
            claimed = runs.claim(session, cancellations_only=not enabled)
            if claimed is None:
                return False
            run, token = claimed
            if run.cancel_requested:
                runs.checkpoint(session, run, token, state="cancelled")
                return True
            actor = session.get(User, run.actor_id) if run.actor_id else None
            if actor is None or not actor.is_active:
                runs.checkpoint(
                    session,
                    run,
                    token,
                    state="failed",
                    failure_code="actor_unavailable",
                )
                return True
            try:
                runs.normalize_scope(
                    session, actor, run.scope, json.loads(run.scope_ids_json)
                )
            except OperationError:
                runs.checkpoint(
                    session,
                    run,
                    token,
                    state="failed",
                    failure_code="scope_permission_changed",
                )
                return True
            progress = json.loads(run.checkpoint_json)
            counters = json.loads(run.counters_json)
            config = SimilaritySettings.model_validate_json(run.settings_json)
            try:
                # Idle/cancellation bookkeeping never reads Artifact bytes.
                # Retain sources only after an authorized work unit is claimed.
                with self._retain_storage():
                    if run.phase == "fingerprint":
                        self._fingerprint(
                            session, run, token, actor, config, progress, counters
                        )
                    elif run.phase == "embeddings":
                        from app.modules.similarity import embeddings

                        embeddings.work_one(
                            self.sessions,
                            session,
                            self.backend,
                            actor,
                            run,
                            token,
                            config,
                            progress,
                            counters,
                        )
                    else:
                        self._candidates(
                            session, run, token, actor, config, progress, counters
                        )
            except OperationError as exc:
                if exc.kind.value in ("busy", "capacity", "unavailable"):
                    runs.checkpoint(session, run, token)
                    return False
                runs.checkpoint(
                    session,
                    run,
                    token,
                    state="failed",
                    failure_code="analysis_unavailable",
                )
            except Exception:
                session.rollback()
                # Native/parser error text may include paths. Retain only an
                # owner-defined failure code and the last committed checkpoint.
                runs.checkpoint(
                    session, run, token, state="failed", failure_code="analysis_failed"
                )
            return True

    def _fingerprint(
        self,
        session: Session,
        run: SimilarityRun,
        token: str,
        actor: User,
        config: SimilaritySettings,
        progress: dict,
        counters: dict,
    ) -> None:
        file = session.exec(
            runs.source_query(session, run, actor)
            .where(File.id > progress.get("file_id", 0))
            .limit(1)
        ).first()
        if file is None:
            runs.checkpoint(
                session,
                run,
                token,
                phase="embeddings" if config.embeddings_enabled else "candidates",
                progress={},
                counters=counters,
            )
            return
        claimed = fingerprints.claim(
            session, file, retry_incomplete=run.trigger == "manual"
        )
        if claimed is None:
            row = session.exec(
                select(GeometryFingerprint).where(
                    GeometryFingerprint.file_id == file.id,
                    GeometryFingerprint.component_index == 0,
                    GeometryFingerprint.source_sha256 == file.sha256,
                    GeometryFingerprint.algorithm_version == run.algorithm_version,
                )
            ).first()
            if row is None or row.state == "pending":
                runs.checkpoint(session, run, token)
                return
            counters["cached"] = counters.get("cached", 0) + 1
        else:
            slot = compute_slots.acquire(session, token)
            if slot is None:
                fingerprints.release(session, claimed[0], claimed[1])
                runs.checkpoint(session, run, token)
                return
            try:
                try:
                    with artifact_content.resolve(
                        file, backend=self.backend
                    ).materialize() as path:
                        if fingerprints.source_digest(path) != file.sha256:
                            result = FingerprintResult(
                                "failed", failure_code="source_changed"
                            )
                            metrics = None
                        else:
                            metrics = ThumbnailEngine().generate(
                                ThumbnailRequest(
                                    path=path,
                                    file_type=file.file_type.value,
                                    include_thumbnail=False,
                                    include_geometry=False,
                                    include_fingerprint=True,
                                    triangle_cap=config.triangle_cap,
                                    reason="similarity",
                                )
                            )
                            result = metrics.fingerprint_result or FingerprintResult(
                                "failed", failure_code="analysis_unavailable"
                            )
                except artifact_content.ArtifactContentChangedError:
                    result, metrics = (
                        FingerprintResult("failed", failure_code="source_changed"),
                        None,
                    )
                except artifact_content.ArtifactContentError:
                    result, metrics = (
                        FingerprintResult("failed", failure_code="source_unavailable"),
                        None,
                    )
                published = fingerprints.publish(
                    session,
                    file,
                    result,
                    fingerprint_id=claimed[0],
                    token=claimed[1],
                    duration_ms=metrics.duration_ms if metrics else None,
                    peak_rss_bytes=metrics.peak_rss_bytes if metrics else None,
                )
                state = result.state if published else "stale"
                counters[state] = counters.get(state, 0) + 1
            finally:
                fingerprints.release(session, claimed[0], claimed[1])
                compute_slots.release(session, slot.id, token)
                session.commit()
        progress["file_id"] = file.id
        counters["artifacts_processed"] = counters.get("artifacts_processed", 0) + 1
        runs.checkpoint(session, run, token, progress=progress, counters=counters)

    def _candidates(
        self,
        session: Session,
        run: SimilarityRun,
        token: str,
        actor: User,
        config: SimilaritySettings,
        progress: dict,
        counters: dict,
    ) -> None:
        pairs = progress.get("pending_pairs", [])
        if pairs:
            slot = compute_slots.acquire(session, token)
            if slot is None:
                runs.checkpoint(session, run, token)
                return
            try:
                self._verify_pair(
                    session, run, token, actor, config, pairs[0], counters
                )
            finally:
                compute_slots.release(session, slot.id, token)
                session.commit()
            progress["pending_pairs"] = pairs[1:]
            runs.checkpoint(session, run, token, progress=progress, counters=counters)
            return
        sources = (
            runs.source_query(session, run, actor)
            .with_only_columns(File.id)
            .order_by(None)
        )
        row = session.exec(
            select(GeometryFingerprint)
            .where(
                col(GeometryFingerprint.file_id).in_(sources),
                GeometryFingerprint.id > progress.get("fingerprint_id", 0),
                GeometryFingerprint.algorithm_version == run.algorithm_version,
                col(GeometryFingerprint.state).in_(("ready", "partial")),
                # A source edit during fingerprinting cannot contribute old rows.
                col(GeometryFingerprint.source_sha256).in_(
                    select(File.sha256).where(File.id == GeometryFingerprint.file_id)
                ),
            )
            .order_by(GeometryFingerprint.id)
            .limit(1)
        ).first()
        if row is None:
            runs.checkpoint(session, run, token, state="completed", counters=counters)
            return
        shortlist = retrieval.find_candidates(
            session, row, actor, limit=config.max_candidates
        )
        if config.embeddings_enabled:
            from app.modules.similarity.learned_retrieval import extend

            shortlist = extend(
                session, self.sessions, actor, row, shortlist, config.max_candidates
            )
        progress.update(
            fingerprint_id=row.id,
            pending_pairs=[[row.id, target] for target in shortlist.fingerprint_ids],
        )
        counters["shortlisted"] = counters.get("shortlisted", 0) + len(
            shortlist.fingerprint_ids
        )
        counters["skipped_by_budget"] = (
            counters.get("skipped_by_budget", 0) + shortlist.skipped_by_budget
        )
        runs.checkpoint(session, run, token, progress=progress, counters=counters)

    def _verify_pair(
        self,
        session: Session,
        run: SimilarityRun,
        token: str,
        actor: User,
        config: SimilaritySettings,
        pair: list[int],
        counters: dict,
    ) -> None:
        first, second = (session.get(GeometryFingerprint, value) for value in pair)
        if first is None or second is None:
            return
        fa, fb = session.get(File, first.file_id), session.get(File, second.file_id)
        if fa is None or fb is None or fa.model_id == fb.model_id:
            return
        if fa.model_id > fb.model_id:
            first, second, fa, fb = second, first, fb, fa
        for file, fp in ((fa, first), (fb, second)):
            if not fingerprints.current_source(session, file.id, fp.source_sha256):
                counters["stale"] = counters.get("stale", 0) + 1
                return
            runs.normalize_scope(session, actor, "models", [file.model_id])
        try:
            with ExitStack() as stack:
                path_a, path_b = (
                    stack.enter_context(
                        artifact_content.resolve(
                            file, backend=self.backend
                        ).materialize()
                    )
                    for file in (fa, fb)
                )
                if (
                    fingerprints.source_digest(path_a) != first.source_sha256
                    or fingerprints.source_digest(path_b) != second.source_sha256
                ):
                    counters["stale"] = counters.get("stale", 0) + 1
                    return
                if verification_cache.reusable_pair(
                    session, first, second, sample_points=config.sample_points
                ):
                    counters["verification_cached"] = (
                        counters.get("verification_cached", 0) + 1
                    )
                    return
                evidence = geometry_analysis.verify_paths(
                    path_a,
                    path_b,
                    first_type=fa.file_type.value,
                    second_type=fb.file_type.value,
                    first_component=first.component_index,
                    second_component=second.component_index,
                    sample_points=config.sample_points,
                    triangle_cap=config.triangle_cap,
                )
        except (GeometryError, artifact_content.ArtifactContentError):
            counters["verification_failed"] = counters.get("verification_failed", 0) + 1
            return
        counters["verified"] = counters.get("verified", 0) + 1
        # Persist measured evidence independently of review thresholds. Changing
        # a slider selects durable candidates without repeating geometry work.
        if evidence.evidence_class is not None:
            kind = (
                "component_match"
                if first.component_index or second.component_index
                else "whole"
            )
            candidate = candidates.publish(
                session, first, second, evidence, run=run, run_token=token, kind=kind
            )
            if candidate is not None and candidate.confidence > 0:
                counters["candidates"] = counters.get("candidates", 0) + 1
