"""Indexed retrieval and bounded descriptor ranking; keys never establish identity."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import false, true, union
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

from app.db.models import CollectionRole, File, GeometryFingerprint, Model, User
from app.modules.identity.rbac import accessible_collection_ids
from app.modules.similarity.fingerprints import live_source_predicates

PREFILTER_LIMIT = 512


@dataclass(frozen=True)
class Shortlist:
    fingerprint_ids: tuple[int, ...]
    examined: int
    skipped_by_budget: int
    hash_hits: tuple[int, ...] = ()


def editable_models(session: Session, user: User, model=Model):
    if user.is_superuser:
        return true()
    ids = accessible_collection_ids(session, user, CollectionRole.EDIT)
    return col(model.collection_id).in_(ids) if ids else false()


def hash_lookup(source: GeometryFingerprint):
    """Each arm is a direct lookup in one of the eight single-key indexes."""
    queries = []
    for group in ("physical", "normalized"):
        for index in range(4):
            column = getattr(GeometryFingerprint, f"{group}_hash_{index}")
            value = getattr(source, f"{group}_hash_{index}")
            if value is not None:
                queries.append(select(GeometryFingerprint.id).where(column == value))
    return union(*queries) if queries else select(GeometryFingerprint.id).where(false())


def find_candidates(
    session: Session,
    source: GeometryFingerprint,
    actor: User,
    *,
    limit: int = 20,
    bucket_limit: int = PREFILTER_LIMIT,
) -> Shortlist:
    if not 1 <= limit <= 100 or not 1 <= bucket_limit <= 2048:
        raise ValueError("invalid_candidate_budget")
    file = session.get(File, source.file_id)
    if (
        file is None
        or session.exec(
            select(Model.id).where(
                Model.id == file.model_id, editable_models(session, actor)
            )
        ).first()
        is None
    ):
        return Shortlist((), 0, 0)
    allowed = (
        GeometryFingerprint.algorithm_version == source.algorithm_version,
        col(GeometryFingerprint.state).in_(("ready", "partial")),
        GeometryFingerprint.source_sha256 == File.sha256,
        Model.id != file.model_id,
        *live_source_predicates(),
        editable_models(session, actor),
    )
    base = (
        select(GeometryFingerprint.id)
        .join(File, File.id == GeometryFingerprint.file_id)
        .join(Model, Model.id == File.model_id)
        .where(*allowed)
    )
    lanes = [base.where(col(GeometryFingerprint.id).in_(hash_lookup(source)))]
    if source.area_volume_ratio is not None:
        strict = base.where(
            col(GeometryFingerprint.area_volume_ratio).between(
                source.area_volume_ratio * 0.98, source.area_volume_ratio * 1.02
            )
        )
        if source.euler_characteristic is not None:
            strict = strict.where(
                GeometryFingerprint.euler_characteristic == source.euler_characteristic
            )
        if source.hull_ratio is not None:
            strict = strict.where(
                col(GeometryFingerprint.hull_ratio).between(
                    source.hull_ratio * 0.97, source.hull_ratio * 1.03
                )
            )
        for index in range(2):
            column = getattr(GeometryFingerprint, f"inertia_ratio_{index}")
            value = getattr(source, f"inertia_ratio_{index}")
            if value is not None:
                strict = strict.where(column.between(value * 0.95, value * 1.05))
        lanes.append(strict)
    # The repair lane intentionally has no Euler, manifold, component-count or
    # triangle-count equality filter. A 25%-face remesh remains eligible.
    if source.normalized_area is not None:
        lanes.append(
            base.where(
                col(GeometryFingerprint.normalized_area).between(
                    source.normalized_area * 0.85, source.normalized_area * 1.15
                )
            )
        )
    candidates: set[int] = set()
    hash_hits: set[int] = set()
    skipped = 0
    for index, lane in enumerate(lanes):
        rows = session.exec(
            lane.order_by(col(GeometryFingerprint.id)).limit(bucket_limit + 1)
        ).all()
        if len(rows) > bucket_limit:
            skipped += 1  # Lower bound, never a fabricated total beyond the cap.
        admitted = {int(row) for row in rows[:bucket_limit] if row is not None}
        candidates.update(admitted)
        if index == 0:
            hash_hits = admitted
    if not candidates:
        return Shortlist((), 0, skipped)
    rows = session.exec(
        select(GeometryFingerprint)
        .where(col(GeometryFingerprint.id).in_(candidates))
        .options(
            load_only(
                GeometryFingerprint.id,
                GeometryFingerprint.volume,
                GeometryFingerprint.d2_blob,
                GeometryFingerprint.sh_blob,
                GeometryFingerprint.view_blob,
            )
        )
    ).all()
    scored = []
    for row in rows:
        score = descriptor_distance(source, row)
        if row.id in hash_hits or score is not None and score <= 0.25:
            scored.append(
                (
                    0 if row.id in hash_hits else 1,
                    score if score is not None else 1.0,
                    row.id,
                )
            )
    scored.sort()
    skipped += max(len(scored) - limit, 0)
    return Shortlist(
        tuple(row[2] for row in scored[:limit]),
        len(rows),
        skipped,
        tuple(row[2] for row in scored[:limit] if row[2] in hash_hits),
    )


def descriptor_distance(
    left: GeometryFingerprint, right: GeometryFingerprint
) -> float | None:
    import numpy as np

    distances = []
    if (
        left.d2_blob is not None
        and right.d2_blob is not None
        and len(left.d2_blob) == len(right.d2_blob) == 256
    ):
        a, b = (
            np.frombuffer(left.d2_blob, dtype="<f4"),
            np.frombuffer(right.d2_blob, dtype="<f4"),
        )
        if np.isfinite(a).all() and np.isfinite(b).all():
            distances.append(
                (0.65, float(0.5 * np.sum((a - b) ** 2 / np.maximum(a + b, 1e-12))))
            )
    if (
        left.volume is not None
        and right.volume is not None
        and left.sh_blob is not None
        and right.sh_blob is not None
        and len(left.sh_blob) == len(right.sh_blob) == 256
    ):
        a, b = (
            np.frombuffer(left.sh_blob, dtype="<f4"),
            np.frombuffer(right.sh_blob, dtype="<f4"),
        )
        if np.isfinite(a).all() and np.isfinite(b).all():
            distances.append((0.25, float(np.clip(1 - a @ b, 0, 2) / 2)))
    if (
        left.view_blob is not None
        and right.view_blob is not None
        and len(left.view_blob) == len(right.view_blob) == 48
    ):
        # Axis permutations/sign choices preserve opposite-view pairs. DCT is
        # a retrieval hint; it cannot assert a reflection or exact equivalence.
        from itertools import permutations, product

        a = np.frombuffer(left.view_blob, np.uint8).reshape(3, 2, 8)
        b = np.frombuffer(right.view_blob, np.uint8).reshape(3, 2, 8)
        best = 384
        for order in permutations(range(3)):
            for flips in product((False, True), repeat=3):
                arranged = b[list(order)].copy()
                for axis, flip in enumerate(flips):
                    if flip:
                        arranged[axis] = arranged[axis, ::-1]
                best = min(best, int(np.unpackbits(a ^ arranged).sum()))
        distances.append((0.1, best / 384))
    if not distances:
        return None
    return sum(weight * score for weight, score in distances) / sum(
        weight for weight, _ in distances
    )
