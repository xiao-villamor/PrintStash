"""One embedding unit per existing SimilarityRun checkpoint; no second scheduler."""

from __future__ import annotations

from contextlib import ExitStack

from printstash_core.inference import EmbeddingError
from printstash_core.mesh.similarity import GeometryError
from sqlmodel import Session, col, select

from app.db.models import File, GeometryFingerprint, SimilarityRun, User
from app.db.session import SessionFactory
from app.modules.inference import store
from app.modules.inference.local import configured_provider
from app.modules.media import compute_slots, geometry_analysis
from app.modules.similarity import fingerprints, runs
from app.modules.similarity.configuration import SimilaritySettings
from app.modules.storage import artifact_content
from app.modules.storage.storage_backend.contracts import StorageBackend


def work_one(
    sessions: SessionFactory,
    session: Session,
    backend: StorageBackend,
    actor: User,
    run: SimilarityRun,
    token: str,
    config: SimilaritySettings,
    progress: dict,
    counters: dict,
) -> None:
    import numpy as np

    fp: GeometryFingerprint | None = None
    try:
        provider = configured_provider(sessions)
        generation_id = progress.get("generation_id")
        if generation_id is None:
            generation_id = store.initialize(sessions, provider)
            progress["generation_id"] = generation_id
            progress["space_hash"] = provider.space.config_hash
            runs.checkpoint(session, run, token, progress=progress)
            return
        if provider.space.config_hash != progress.get("space_hash"):
            raise EmbeddingError("embedding_configuration_changed")
        sources = (
            runs.source_query(session, run, actor)
            .with_only_columns(File.id)
            .order_by(None)
        )
        fp = session.exec(
            select(GeometryFingerprint)
            .where(
                col(GeometryFingerprint.file_id).in_(sources),
                GeometryFingerprint.id > progress.get("embedding_fingerprint_id", 0),
                GeometryFingerprint.algorithm_version == run.algorithm_version,
                GeometryFingerprint.state == "ready",
            )
            .order_by(GeometryFingerprint.id)
            .limit(1)
        ).first()
        if fp is None:
            runs.checkpoint(
                session,
                run,
                token,
                phase="candidates",
                progress=progress,
                counters=counters,
            )
            return
        file = session.get(File, fp.file_id)
        key = store.unit_key(
            fp.file_id,
            fp.component_index,
            fp.source_sha256,
            provider.space.render_recipe,
        )
        if file is None or not fingerprints.current_source(
            session, fp.file_id, fp.source_sha256
        ):
            counters["embedding_stale"] = counters.get("embedding_stale", 0) + 1
        elif store.has_unit(session, generation_id, key):
            counters["embedding_cached"] = counters.get("embedding_cached", 0) + 1
        else:
            slot = compute_slots.acquire(session, token)
            if slot is None:
                runs.checkpoint(session, run, token)
                return
            try:
                with ExitStack() as cleanup:
                    path = cleanup.enter_context(
                        artifact_content.resolve(file, backend=backend).materialize()
                    )
                    if fingerprints.source_digest(path) != fp.source_sha256:
                        raise GeometryError("source_changed")
                    views = geometry_analysis.embedding_views(
                        path,
                        file_type=file.file_type.value,
                        component_index=fp.component_index,
                        image_size=provider.manifest.image.image_size,
                        triangle_cap=config.triangle_cap,
                    )
            finally:
                compute_slots.release(session, slot.id, token)
                session.commit()
            vectors = provider.embed(views, provider.space)
            pooled = np.mean(np.asarray(vectors, dtype=np.float64), axis=0)
            if store.publish(
                session,
                actor,
                generation_id=generation_id,
                space=provider.space,
                file_id=fp.file_id,
                component_index=fp.component_index,
                input_hash=fp.source_sha256,
                vector=pooled,
                run_id=run.id,
                lease_token=token,
            ):
                counters["embedded"] = counters.get("embedded", 0) + 1
        progress["embedding_fingerprint_id"] = fp.id
        runs.checkpoint(session, run, token, progress=progress, counters=counters)
    except (GeometryError, artifact_content.ArtifactContentError):
        counters["embedding_failed"] = counters.get("embedding_failed", 0) + 1
        if fp is not None:
            progress["embedding_fingerprint_id"] = fp.id
        runs.checkpoint(session, run, token, progress=progress, counters=counters)
    except EmbeddingError as exc:
        if exc.code == "embedding_compute_busy":
            runs.checkpoint(session, run, token)
            return
        # Optional learned retrieval never invalidates completed geometry work.
        progress["embedding_failure_code"] = exc.code
        runs.checkpoint(
            session,
            run,
            token,
            progress=progress,
            counters=counters,
            phase="candidates",
        )
