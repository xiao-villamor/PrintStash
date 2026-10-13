"""Bounded prepared components with one checkpoint and publication fence per unit."""

from __future__ import annotations

from printstash_core.inference import EmbeddingError
from printstash_core.mesh.similarity import GeometryError
from sqlmodel import Session, col, select

from app.db.models import File, GeometryFingerprint, SimilarityRun, User
from app.db.session import SessionFactory
from app.modules.inference.local import configured_provider
from app.modules.inference.manifest import LocalModelManifest, PointModelManifest
from app.modules.media import embedding_isolation
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.source_preparation import prepare_sources
from app.modules.similarity import fingerprints, runs
from app.modules.similarity import vector_sources as store
from app.modules.similarity.configuration import SimilaritySettings
from app.modules.storage import artifact_content
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.runtime.native_admission import AdmissionTooLarge


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

    assert run.id is not None
    fp: GeometryFingerprint | None = None
    try:
        provider = configured_provider(sessions)
        manifest = provider.manifest
        if not isinstance(manifest, LocalModelManifest | PointModelManifest):
            raise EmbeddingError("embedding_visual_model_required")
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
            .with_only_columns(col(File.id))
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
            .order_by(col(GeometryFingerprint.id))
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
            from app.modules.inference.batches import embed_units

            candidates = session.exec(
                select(GeometryFingerprint)
                .where(
                    col(GeometryFingerprint.file_id).in_(sources),
                    GeometryFingerprint.id > fp.id,
                    GeometryFingerprint.algorithm_version == run.algorithm_version,
                    GeometryFingerprint.state == "ready",
                )
                .order_by(col(GeometryFingerprint.id))
                .limit(1)
            ).all()
            units = [fp]
            if candidates:
                candidate = candidates[0]
                candidate_key = store.unit_key(
                    candidate.file_id,
                    candidate.component_index,
                    candidate.source_sha256,
                    provider.space.render_recipe,
                )
                if (
                    candidate.file_id == fp.file_id
                    and candidate.source_sha256 == fp.source_sha256
                    and not store.has_unit(session, generation_id, candidate_key)
                ):
                    units.append(candidate)
            with prepare_sources(
                (artifact_content.resolve(file, backend=backend),)
            ) as (path,):
                if fingerprints.source_digest(path) != fp.source_sha256:
                    raise GeometryError("source_changed")
                if len(units) == 1:
                    prepared_units = (
                        embedding_isolation.embedding_views(
                            path,
                            file_type=file.file_type.value,
                            component_index=fp.component_index,
                            image_size=manifest.image.image_size,
                            triangle_cap=config.triangle_cap,
                        ),
                    )
                else:
                    prepared_units = embedding_isolation.embedding_components(
                        path,
                        file_type=file.file_type.value,
                        component_indices=tuple(unit.component_index for unit in units),
                        image_size=manifest.image.image_size,
                        triangle_cap=config.triangle_cap,
                    )
            valid = [
                (unit, views)
                for unit, views in zip(units, prepared_units, strict=True)
                if not isinstance(views, GeometryError)
            ]
            results = iter(
                embed_units(
                    provider, tuple(views for _, views in valid), provider.space
                )
                if valid
                else ()
            )
            for unit, views in zip(units, prepared_units, strict=True):
                if isinstance(views, GeometryError):
                    counters["embedding_failed"] = (
                        counters.get("embedding_failed", 0) + 1
                    )
                else:
                    vectors = next(results)
                    if isinstance(vectors, EmbeddingError):
                        counters["embedding_failed"] = (
                            counters.get("embedding_failed", 0) + 1
                        )
                    else:
                        pooled = np.mean(np.asarray(vectors, dtype=np.float64), axis=0)
                        if store.publish(
                            session,
                            actor,
                            generation_id=generation_id,
                            space=provider.space,
                            file_id=unit.file_id,
                            component_index=unit.component_index,
                            input_hash=unit.source_sha256,
                            vector=pooled,
                            run_id=run.id,
                            writer=token,
                        ):
                            counters["embedded"] = counters.get("embedded", 0) + 1
                progress["embedding_fingerprint_id"] = unit.id
                runs.checkpoint(
                    session, run, token, progress=progress, counters=counters
                )
            return
        progress["embedding_fingerprint_id"] = fp.id
        runs.checkpoint(session, run, token, progress=progress, counters=counters)
    except (
        GeometryError,
        AdmissionTooLarge,
        MeshWorkerError,
        artifact_content.ArtifactContentError,
    ):
        # A worker killed for this model's bytes fails this unit, not the run.
        counters["embedding_failed"] = counters.get("embedding_failed", 0) + 1
        if fp is not None:
            progress["embedding_fingerprint_id"] = fp.id
        runs.checkpoint(session, run, token, progress=progress, counters=counters)
    except EmbeddingError as exc:
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
