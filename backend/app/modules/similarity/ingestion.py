"""Optional derivative publication after ingestion has committed its Artifact."""

from __future__ import annotations

from sqlmodel import Session

from app.core.errors import OperationError
from app.db.models import File, JobState, User
from app.db.session import SessionFactory
from app.modules.ingestion.extensions import (
    MeshExtractionOptions,
    MeshFingerprintPublication,
    MeshFingerprintPublished,
)
from app.modules.media.fingerprints import FingerprintResult, FingerprintResultState
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import (
    publish_precomputed,
    publish_precomputed_in_transaction,
)
from app.modules.similarity.runs import start, start_in_transaction, system_actor
from app.modules.work.contracts import JobExecution
from app.modules.work.jobs import jobs


def extraction_options(sessions: SessionFactory) -> MeshExtractionOptions:
    with sessions.scoped_session() as session:
        try:
            config = read_settings(session)
        except OperationError:
            # A broken derivative setting cannot prevent committing source bytes.
            return {}
        return (
            {"include_fingerprint": True, "triangle_cap": config.triangle_cap}
            if config.enabled and config.fingerprint_on_ingest
            else {}
        )


def after_commit(
    sessions: SessionFactory,
    file_id: int,
    actor_id: int | None,
    result: FingerprintResult,
    *,
    source_sha256: str,
    execution: JobExecution | None = None,
) -> str:
    with sessions.scoped_session() as session:
        file = session.get(File, file_id)
        if file is None or file.sha256 != source_sha256:
            return "stale"
        state = publish_precomputed(session, file, result)
        # The content/algorithm keyed cache is immutable once ready. It remains
        # useful after cancellation, but a retired execution cannot create work.
        if (
            execution is not None
            and jobs.lock_execution(
                session,
                execution.job_id,
                epoch=execution.execution_epoch,
                attempt=execution.attempt,
                states=(JobState.RUNNING,),
            )
            is None
        ):
            return state
        # A derivative has no requesting user; its run is a system run.
        actor = session.get(User, actor_id) if actor_id else system_actor(session)
        if state in ("ready", "partial") and actor is not None and actor.is_active:
            try:
                start(
                    session,
                    actor,
                    scope="models",
                    ids=[file.model_id],
                    trigger="ingest",
                )
            except OperationError as exc:
                if exc.code not in (
                    "similarity_run_active",
                    "similarity_disabled",
                    "similarity_scope_unavailable",
                ):
                    raise
            else:
                from app.db.models import JobKind
                from app.modules.work import nudge

                nudge(JobKind.SIMILARITY_ANALYZE)
        return state


def publish_mesh_fingerprint_continuation(
    session: Session,
    file: File,
    result: FingerprintResult,
) -> MeshFingerprintPublication:
    """Cache and durable system-run intent share the derivative owner's commit."""
    state = publish_precomputed_in_transaction(session, file, result)
    if not isinstance(state, MeshFingerprintPublished) or state.state not in (
        FingerprintResultState.READY,
        FingerprintResultState.PARTIAL,
    ):
        return state
    actor = system_actor(session)
    if actor is None or not actor.is_active:
        return state
    try:
        start_in_transaction(
            session, actor, scope="models", ids=[file.model_id], trigger="ingest"
        )
    except OperationError as exc:
        if exc.code not in (
            "similarity_run_active",
            "similarity_disabled",
            "similarity_scope_unavailable",
        ):
            raise
    return state
