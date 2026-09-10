"""Optional semantic retrieval adds proposals, never an equivalence decision."""

from __future__ import annotations

from printstash_core.inference import EmbeddingError
from sqlmodel import Session, col, select

from app.db.models import GeometryFingerprint, PassageVector, User
from app.db.session import SessionFactory
from app.modules.inference import store
from app.modules.inference.local import configured_provider
from app.modules.similarity.retrieval import Shortlist


def extend(
    session: Session,
    sessions: SessionFactory,
    actor: User,
    source: GeometryFingerprint,
    shortlist: Shortlist,
    limit: int,
) -> Shortlist:
    import numpy as np

    try:
        provider = configured_provider(sessions)
        generation = store.active_generation(session, provider.space)
        if generation is None:
            return shortlist
        key = store.unit_key(
            source.file_id,
            source.component_index,
            source.source_sha256,
            provider.space.render_recipe,
        )
        vector = session.exec(
            select(PassageVector).where(
                PassageVector.generation_id == generation.id,
                PassageVector.unit_key == key,
            )
        ).first()
        if vector is None:
            return shortlist
        neighbors = store.query(
            session,
            actor,
            generation_id=generation.id,
            space=provider.space,
            vector=np.frombuffer(vector.vector_blob, dtype="<f4"),
            limit=limit,
            exclude_model_id=vector.model_id,
        )
        targets = {
            row.id: row
            for row in session.exec(
                select(PassageVector).where(
                    col(PassageVector.id).in_(
                        [item.unit_id for item in neighbors.items]
                    )
                )
            ).all()
        }
        proposed = []
        for neighbor in neighbors.items:
            row = targets[neighbor.unit_id]
            component_index = store.unit_component(row.unit_key)
            if component_index is None:
                continue
            fp = session.exec(
                select(GeometryFingerprint.id).where(
                    GeometryFingerprint.file_id == row.file_id,
                    GeometryFingerprint.component_index == component_index,
                    GeometryFingerprint.source_sha256 == row.input_hash,
                    GeometryFingerprint.algorithm_version == source.algorithm_version,
                    GeometryFingerprint.state == "ready",
                )
            ).first()
            if fp is not None:
                proposed.append(fp)
        # Exact-key proposals stay first, while interleaving gives both geometric
        # and learned retrieval a bounded share of the verification budget.
        combined = list(shortlist.hash_hits)
        for index in range(max(len(shortlist.fingerprint_ids), len(proposed))):
            for sequence in (shortlist.fingerprint_ids, proposed):
                if index < len(sequence) and sequence[index] not in combined:
                    combined.append(sequence[index])
        skipped = (
            shortlist.skipped_by_budget
            + max(len(combined) - limit, 0)
            + int(neighbors.truncated)
        )
        return Shortlist(
            tuple(combined[:limit]),
            shortlist.examined + neighbors.scanned,
            skipped,
            shortlist.hash_hits,
        )
    except EmbeddingError:
        return shortlist
