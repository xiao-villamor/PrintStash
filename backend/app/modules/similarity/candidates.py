"""Authorized candidate projection, current evidence and monotonic publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from printstash_core.mesh.similarity.verification import Verification
from sqlalchemy import and_, case, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, or_, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    GeometryFingerprint,
    Model,
    SimilarityCandidate,
    SimilarityCandidateObservation,
    SimilarityRun,
    User,
)
from app.db.scopes import live
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import (
    current_source,
    encode_json,
    live_source_predicates,
)
from app.modules.similarity.retrieval import editable_models


@dataclass(frozen=True)
class CandidatePage:
    items: list[dict]
    next_cursor: str | None


def current_evidence(candidate=SimilarityCandidate):
    """Correlated SQL predicate; source lifecycle invalidation needs no sweep."""
    a, b = aliased(GeometryFingerprint), aliased(GeometryFingerprint)
    fa, fb = aliased(File), aliased(File)
    ma, mb = aliased(Model), aliased(Model)
    observation = SimilarityCandidateObservation
    return (
        select(observation.id)
        .join(a, a.id == observation.fingerprint_a_id)
        .join(b, b.id == observation.fingerprint_b_id)
        .join(fa, fa.id == a.file_id)
        .join(fb, fb.id == b.file_id)
        .join(ma, ma.id == fa.model_id)
        .join(mb, mb.id == fb.model_id)
        .where(
            observation.candidate_id == candidate.id,
            or_(
                col(candidate.primary_lineage_key).is_(None),
                observation.lineage_key == candidate.primary_lineage_key,
            ),
            fa.model_id == candidate.model_a_id,
            fb.model_id == candidate.model_b_id,
            fa.sha256 == observation.input_hash_a,
            fb.sha256 == observation.input_hash_b,
            a.source_sha256 == fa.sha256,
            b.source_sha256 == fb.sha256,
            a.algorithm_version == candidate.algorithm_version,
            b.algorithm_version == candidate.algorithm_version,
            col(a.state).in_(("ready", "partial")),
            col(b.state).in_(("ready", "partial")),
            *live_source_predicates(fa, ma),
            *live_source_predicates(fb, mb),
        )
        .correlate(candidate)
        .exists()
    )


def visible_query(session: Session, actor: User):
    a, b = aliased(Model), aliased(Model)
    return (
        select(SimilarityCandidate)
        .join(a, a.id == SimilarityCandidate.model_a_id)
        .join(b, b.id == SimilarityCandidate.model_b_id)
        .where(
            SimilarityCandidate.confidence > 0,
            live(a),
            live(b),
            col(a.purge_token).is_(None),
            col(b.purge_token).is_(None),
            editable_models(session, actor, a),
            editable_models(session, actor, b),
        )
    )


def require(session: Session, actor: User, candidate_id: int) -> SimilarityCandidate:
    row = session.exec(
        visible_query(session, actor).where(SimilarityCandidate.id == candidate_id)
    ).first()
    if row is None:
        raise OperationError("similarity_candidate_not_found", kind=ErrorKind.NOT_FOUND)
    return row


def is_current(session: Session, row: SimilarityCandidate) -> bool:
    return (
        session.exec(
            select(SimilarityCandidate.id).where(
                SimilarityCandidate.id == row.id, current_evidence()
            )
        ).first()
        is not None
    )


def model_references(session: Session, ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    return {
        model.id: {
            "id": model.id,
            "name": model.name,
            "slug": model.slug,
            "thumbnail_file_id": model.thumbnail_file_id,
        }
        for model in session.exec(select(Model).where(col(Model.id).in_(ids))).all()
        if model.id is not None
    }


def project(
    session: Session,
    row: SimilarityCandidate,
    *,
    detail: bool = False,
    current: bool | None = None,
    references: dict[int, dict] | None = None,
) -> dict:
    if current is None:
        current = is_current(session, row)
    if references is None:
        references = model_references(session, {row.model_a_id, row.model_b_id})
    result = row.model_dump(exclude={"summary_json"})
    result.update(
        model_a=references[row.model_a_id],
        model_b=references[row.model_b_id],
        summary=json.loads(row.summary_json),
        freshness="current" if current else "stale",
        stale_reason=None if current else "source_changed_or_unavailable",
    )
    result["allowed_actions"] = ["reject", "later", "reopen"]
    if current:
        result["allowed_actions"].append("confirm_evidence")
        if row.evidence_class in ("component_of", "plate_of"):
            result["allowed_actions"].append("create_multipart")
    if detail:
        observations = session.exec(
            select(SimilarityCandidateObservation)
            .where(SimilarityCandidateObservation.candidate_id == row.id)
            .order_by(
                case(
                    (
                        SimilarityCandidateObservation.lineage_key
                        == row.primary_lineage_key,
                        0,
                    ),
                    else_=1,
                ),
                SimilarityCandidateObservation.id,
            )
            .limit(101)
        ).all()
        fingerprint_ids = {
            value
            for obs in observations[:100]
            for value in (obs.fingerprint_a_id, obs.fingerprint_b_id)
            if value is not None
        }
        sources = (
            {
                fp.id: {
                    "file_id": fp.file_id,
                    "component_index": fp.component_index,
                    "surface_area": fp.surface_area,
                    "volume": fp.volume,
                    "face_count": fp.face_count,
                    "watertight": fp.watertight,
                }
                for fp in session.exec(
                    select(GeometryFingerprint).where(
                        col(GeometryFingerprint.id).in_(fingerprint_ids)
                    )
                ).all()
            }
            if fingerprint_ids
            else {}
        )
        result["observations"] = [
            obs.model_dump(exclude={"evidence_json"})
            | {
                "evidence": json.loads(obs.evidence_json),
                "source_a": sources.get(obs.fingerprint_a_id),
                "source_b": sources.get(obs.fingerprint_b_id),
            }
            for obs in observations[:100]
        ]
        result["observations_truncated"] = len(observations) > 100
    return result


def list_visible(
    session: Session,
    actor: User,
    *,
    limit: int = 100,
    cursor: str | None = None,
    evidence_class: str | None = None,
    review_state: str | None = None,
    freshness: str | None = None,
    model_id: int | None = None,
    minimum_confidence: float | None = None,
    collection_id: int | None = None,
    file_type: FileType | None = None,
    source: str | None = None,
    known_good: bool | None = None,
) -> CandidatePage:
    if not 1 <= limit <= 1000:
        raise OperationError("similarity_page_invalid")
    query = visible_query(session, actor)
    if minimum_confidence is None:
        config = read_settings(session)
        query = query.where(
            selection_predicate(config.minimum_confidence, config.class_overrides)
        )
    else:
        query = query.where(SimilarityCandidate.confidence >= minimum_confidence)
    if evidence_class:
        query = query.where(SimilarityCandidate.evidence_class == evidence_class)
    if review_state:
        query = query.where(SimilarityCandidate.review_state == review_state)
    if freshness:
        query = query.where(
            current_evidence() if freshness == "current" else ~current_evidence()
        )
    if model_id:
        query = query.where(
            or_(
                SimilarityCandidate.model_a_id == model_id,
                SimilarityCandidate.model_b_id == model_id,
            )
        )
    endpoints = (SimilarityCandidate.model_a_id, SimilarityCandidate.model_b_id)
    if collection_id is not None:
        collection = session.get(Collection, collection_id)
        if collection is None:
            return CandidatePage([], None)
        escaped = (
            collection.path.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        collection_ids = select(Collection.id).where(
            or_(
                Collection.id == collection_id,
                col(Collection.path).like(escaped + "/%", escape="\\"),
            )
        )
        member_ids = select(Model.id).where(
            col(Model.collection_id).in_(collection_ids)
        )
        query = query.where(
            or_(*(col(endpoint).in_(member_ids) for endpoint in endpoints))
        )
    if file_type is not None or source is not None:
        matching_file = (
            select(File.id)
            .join(Model, Model.id == File.model_id)
            .where(
                or_(
                    File.model_id == SimilarityCandidate.model_a_id,
                    File.model_id == SimilarityCandidate.model_b_id,
                ),
                *live_source_predicates(),
            )
        )
        if file_type is not None:
            matching_file = matching_file.where(File.file_type == file_type)
        if source is not None:
            matching_file = matching_file.where(
                File.is_external == (source == "external")
            )
        query = query.where(matching_file.correlate(SimilarityCandidate).exists())
    if known_good is not None:
        good_revision = (
            select(File.id)
            .join(Model, Model.id == File.model_id)
            .where(
                or_(
                    File.model_id == SimilarityCandidate.model_a_id,
                    File.model_id == SimilarityCandidate.model_b_id,
                ),
                File.file_type == FileType.GCODE,
                File.revision_status == FileRevisionStatus.KNOWN_GOOD,
                *live_source_predicates(),
            )
            .correlate(SimilarityCandidate)
            .exists()
        )
        query = query.where(good_revision if known_good else ~good_revision)
    if cursor:
        try:
            score, row_id = json.loads(cursor)
            if (
                type(score) not in (float, int)
                or not 0 <= score <= 1
                or type(row_id) is not int
                or not 1 <= row_id < 2**63
            ):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise OperationError("similarity_cursor_invalid") from exc
        query = query.where(
            or_(
                SimilarityCandidate.confidence < score,
                and_(
                    SimilarityCandidate.confidence == score,
                    SimilarityCandidate.id > row_id,
                ),
            )
        )
    rows = session.exec(
        query.order_by(
            col(SimilarityCandidate.confidence).desc(), SimilarityCandidate.id
        ).limit(limit + 1)
    ).all()
    visible = rows[:limit]
    next_cursor = (
        encode_json([visible[-1].confidence, visible[-1].id])
        if len(rows) > limit
        else None
    )
    ids = [row.id for row in visible]
    current_ids = (
        set(
            session.exec(
                select(SimilarityCandidate.id).where(
                    col(SimilarityCandidate.id).in_(ids), current_evidence()
                )
            ).all()
        )
        if ids
        else set()
    )
    references = model_references(
        session,
        {model_id for row in visible for model_id in (row.model_a_id, row.model_b_id)},
    )
    return CandidatePage(
        [
            project(session, row, current=row.id in current_ids, references=references)
            for row in visible
        ],
        next_cursor,
    )


def publish(
    session: Session,
    first: GeometryFingerprint,
    second: GeometryFingerprint,
    evidence: Verification,
    *,
    run: SimilarityRun | None = None,
    run_token: str | None = None,
    kind: str = "whole",
    contained_side: str | None = None,
) -> SimilarityCandidate | None:
    """Verified inputs only. Preserve human state while adding distinct lineage."""
    if evidence.evidence_class is None:
        return None
    fa, fb = session.get(File, first.file_id), session.get(File, second.file_id)
    if fa is None or fb is None or fa.model_id == fb.model_id:
        return None
    if fa.model_id > fb.model_id:
        raise ValueError("similarity_verification_requires_ordered_models")
    if first.algorithm_version != second.algorithm_version:
        return None
    if not current_source(session, fa.id, first.source_sha256) or not current_source(
        session, fb.id, second.source_sha256
    ):
        return None
    if evidence.exact_equivalence and (
        first.state != "ready" or second.state != "ready"
    ):
        raise ValueError("partial_evidence_cannot_be_exact")
    query = select(SimilarityCandidate).where(
        SimilarityCandidate.model_a_id == fa.model_id,
        SimilarityCandidate.model_b_id == fb.model_id,
        SimilarityCandidate.algorithm_version == first.algorithm_version,
    )
    row = session.exec(query).first()
    if row is None:
        older = session.exec(
            select(SimilarityCandidate.id)
            .where(
                SimilarityCandidate.model_a_id == fa.model_id,
                SimilarityCandidate.model_b_id == fb.model_id,
            )
            .order_by(col(SimilarityCandidate.id).desc())
            .limit(1)
        ).first()
        try:
            with session.begin_nested():
                row = SimilarityCandidate(
                    model_a_id=fa.model_id,
                    model_b_id=fb.model_id,
                    algorithm_version=first.algorithm_version,
                    evidence_class=evidence.evidence_class,
                    reconsidered_candidate_id=older,
                )
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.exec(query).one()
    lineage = hashlib.sha256(
        encode_json(
            [
                first.id,
                second.id,
                first.source_sha256,
                second.source_sha256,
                kind,
                contained_side,
                evidence.version,
                evidence.sample_points,
            ]
        ).encode()
    ).hexdigest()
    existing = session.exec(
        select(SimilarityCandidateObservation).where(
            SimilarityCandidateObservation.candidate_id == row.id,
            SimilarityCandidateObservation.lineage_key == lineage,
        )
    ).first()
    # Publication must recheck both sources and the run token in the writing SQL,
    # even if they changed after the first cheap read above.
    source_checks = []
    for fp in (first, second):
        source_checks.append(
            select(File.id)
            .join(Model, Model.id == File.model_id)
            .where(
                File.id == fp.file_id,
                File.sha256 == fp.source_sha256,
                *live_source_predicates(),
            )
            .exists()
        )
    run_fence = true()
    if run is not None:
        run_fence = (
            select(SimilarityRun.id)
            .where(
                SimilarityRun.id == run.id,
                SimilarityRun.lease_token == run_token,
                col(SimilarityRun.lease_expires_at) > utcnow(),
                col(SimilarityRun.cancel_requested).is_(False),
            )
            .exists()
        )
    values = {"updated_at": utcnow(), "freshness": "current", "stale_reason": None}
    if kind != "component_match" and (
        evidence.confidence > row.confidence
        or not is_current(session, row)
        or not row.summary_json
        or row.summary_json == "{}"
    ):
        values.update(
            evidence_class=evidence.evidence_class,
            confidence=evidence.confidence,
            exact_equivalence=evidence.exact_equivalence,
            primary_lineage_key=lineage,
            summary_json=encode_json(asdict(evidence)),
            run_id=run.id if run else None,
        )
    if existing is None:
        values["version"] = col(SimilarityCandidate.version) + (
            0 if row.summary_json == "{}" else 1
        )
    changed = session.connection().execute(
        update(SimilarityCandidate)
        .where(SimilarityCandidate.id == row.id, *source_checks, run_fence)
        .values(**values)
    )
    if changed.rowcount != 1:
        session.rollback()
        return None
    if existing is None:
        session.add(
            SimilarityCandidateObservation(
                candidate_id=row.id,
                fingerprint_a_id=first.id,
                fingerprint_b_id=second.id,
                lineage_key=lineage,
                input_hash_a=first.source_sha256,
                input_hash_b=second.source_sha256,
                kind=kind,
                contained_side=contained_side,
                multiplicity_a=first.instance_count,
                multiplicity_b=second.instance_count,
                evidence_json=encode_json(asdict(evidence)),
            )
        )
    if kind == "component_match":
        from app.modules.similarity.composition import summarize

        session.flush()
        summary = summarize(session, row.id, fa, fb, first.algorithm_version)
        if summary is not None and (
            row.confidence < summary["confidence"] or not is_current(session, row)
        ):
            session.connection().execute(
                update(SimilarityCandidate)
                .where(SimilarityCandidate.id == row.id)
                .values(
                    evidence_class=summary["evidence_class"],
                    confidence=summary["confidence"],
                    exact_equivalence=False,
                    primary_lineage_key=summary["lineage_key"],
                    summary_json=encode_json(summary),
                )
            )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Another worker published exactly this lineage; the unique constraint
        # keeps retries idempotent without mutating any review decision.
        return session.exec(query).first()
    session.refresh(row)
    return row


def selection_predicate(minimum_confidence: float, overrides: dict):
    return or_(
        and_(
            col(SimilarityCandidate.evidence_class).not_in(overrides),
            SimilarityCandidate.confidence >= minimum_confidence,
        ),
        *(
            and_(
                SimilarityCandidate.evidence_class == key,
                SimilarityCandidate.confidence >= value,
            )
            for key, value in overrides.items()
        ),
    )
