"""Optional derivative publication after ingestion has committed its Artifact."""

from __future__ import annotations

from app.core.errors import OperationError
from app.db.models import File, User
from app.db.session import SessionFactory
from app.modules.ingestion.extensions import MeshExtractionOptions
from app.modules.media.fingerprints import FingerprintResult
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import publish_precomputed
from app.modules.similarity.runs import start


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
    result: FingerprintResult | None,
) -> str:
    with sessions.scoped_session() as session:
        file = session.get(File, file_id)
        if file is None:
            return "stale"
        actor = session.get(User, actor_id) if actor_id else None
        if result is None:
            config = read_settings(session)
            if (
                not config.enabled
                or not config.fingerprint_on_ingest
                or actor is None
                or not actor.is_active
            ):
                return "skipped"
            state = "pending"
        else:
            state = publish_precomputed(session, file, result)
        if (
            state in ("ready", "partial", "pending")
            and actor is not None
            and actor.is_active
        ):
            try:
                start(
                    session,
                    actor,
                    scope="models",
                    ids=[file.model_id],
                    trigger="ingest",
                    ingest_file_id=file.id if result is None else None,
                )
            except OperationError as exc:
                if exc.code not in (
                    "similarity_run_active",
                    "similarity_disabled",
                    "similarity_scope_unavailable",
                ):
                    raise
                if state == "pending" and exc.code != "similarity_run_active":
                    return "skipped"
        return state
