"""Append-only human decisions with optimistic concurrency and atomic resolution."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    SimilarityCandidate,
    SimilarityReviewDecision,
    User,
)
from app.modules.similarity import candidates
from app.modules.similarity.fingerprints import encode_json
from app.schemas.multipart_models import MultipartPartWrite


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    version: int = Field(ge=1, le=2**63 - 1, strict=True)
    action: Literal[
        "confirm_evidence",
        "confirm_family",
        "create_multipart",
        "reject",
        "later",
        "reopen",
    ]
    target_id: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    collection_id: int | None = Field(default=None, gt=0, le=2**63 - 1, strict=True)
    parts: list[MultipartPartWrite] | None = Field(
        default=None, min_length=1, max_length=100
    )


def decide(
    session: Session, actor: User, candidate_id: int, request: DecisionRequest
) -> SimilarityReviewDecision:
    row = candidates.require(session, actor, candidate_id)
    if request.action == "confirm_family":
        raise OperationError("family_resolution_unavailable", kind=ErrorKind.CONFLICT)
    digest = hashlib.sha256(
        encode_json([candidate_id, request.model_dump()]).encode()
    ).hexdigest()
    replay_query = select(SimilarityReviewDecision).where(
        SimilarityReviewDecision.actor_id == actor.id,
        SimilarityReviewDecision.request_id == request.request_id,
    )
    existing = session.exec(replay_query).first()
    if existing is not None:
        if existing.request_hash != digest:
            raise OperationError("similarity_request_conflict", kind=ErrorKind.CONFLICT)
        return existing
    if request.version != row.version:
        raise OperationError("similarity_version_conflict", kind=ErrorKind.CONFLICT)
    resolution = {
        "confirm_evidence": "evidence_only",
        "create_multipart": "multipart",
    }.get(request.action)
    after = {
        "confirm_evidence": "confirmed",
        "create_multipart": "confirmed",
        "reject": "rejected",
        "later": "later",
        "reopen": "open",
    }[request.action]
    if resolution and not candidates.is_current(session, row):
        raise OperationError("similarity_evidence_stale", kind=ErrorKind.CONFLICT)
    if request.action != "create_multipart" and any(
        value is not None
        for value in (
            request.target_id,
            request.name,
            request.collection_id,
            request.parts,
        )
    ):
        raise OperationError("similarity_resolution_payload_invalid")
    snapshot = candidates.project(session, row, detail=True)
    conditions = [
        SimilarityCandidate.id == row.id,
        SimilarityCandidate.version == request.version,
    ]
    # Apply the visibility predicate in the writing statement, not only the
    # preceding read; changes to permissions/source must fence confirmation.
    conditions.append(
        col(SimilarityCandidate.id).in_(
            candidates.visible_query(session, actor).with_only_columns(
                SimilarityCandidate.id
            )
        )
    )
    if resolution:
        conditions.append(candidates.current_evidence())
    changed = session.connection().execute(
        update(SimilarityCandidate)
        .where(*conditions)
        .values(
            review_state=after,
            resolution_kind=resolution,
            reviewer_id=actor.id,
            reviewed_at=utcnow(),
            updated_at=utcnow(),
            version=col(SimilarityCandidate.version) + 1,
        )
    )
    if changed.rowcount != 1:
        session.rollback()
        raise OperationError("similarity_version_conflict", kind=ErrorKind.CONFLICT)
    target_id = None
    try:
        if request.action == "create_multipart":
            target_id = _resolve_multipart(session, actor, row, request)
        # Snapshot is independent of cascading evidence rows. It intentionally
        # carries only the bounded public projection, never a source path.
        decision = SimilarityReviewDecision(
            candidate_id=row.id,
            actor_id=actor.id,
            request_id=request.request_id,
            action=request.action,
            resolution_kind=resolution,
            target_id=target_id,
            before_state=snapshot["review_state"],
            after_state=after,
            candidate_version=request.version,
            snapshot_json=encode_json(
                json.loads(
                    json.dumps(snapshot, default=lambda value: value.isoformat())
                )
            ),
            request_hash=digest,
        )
        session.add(decision)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.exec(replay_query).first()
        if existing is not None and existing.request_hash == digest:
            return existing
        raise OperationError(
            "similarity_resolution_conflict", kind=ErrorKind.CONFLICT
        ) from exc
    except Exception:
        session.rollback()
        raise
    session.refresh(decision)
    return decision


def _resolve_multipart(
    session: Session, actor: User, row: SimilarityCandidate, request: DecisionRequest
) -> int:
    from app.modules.library import multipart_models

    if row.evidence_class not in ("component_of", "plate_of") or not request.parts:
        raise OperationError(
            "similarity_multipart_requires_composition", kind=ErrorKind.CONFLICT
        )
    requested_ids = {
        choice.model_id for part in request.parts for choice in (part.choices or [])
    } | {model_id for part in request.parts for model_id in (part.model_ids or [])}
    if not requested_ids <= {row.model_a_id, row.model_b_id}:
        raise OperationError("similarity_multipart_unrelated_member")
    # The composition proposal is explicit. Quantity/direction are rechecked
    # against the persisted component evidence, never inferred from a thumbnail.
    summary = json.loads(row.summary_json)
    expected = summary.get("composition")
    if not isinstance(expected, list) or not expected:
        raise OperationError(
            "similarity_multipart_evidence_missing", kind=ErrorKind.CONFLICT
        )
    actual = sorted(
        (model_id, part.quantity)
        for part in request.parts
        for model_id in (
            part.model_ids or [choice.model_id for choice in part.choices or []]
        )
    )
    if actual != sorted((item["model_id"], item["quantity"]) for item in expected):
        raise OperationError(
            "similarity_multipart_composition_changed", kind=ErrorKind.CONFLICT
        )
    try:
        if request.target_id is None:
            if request.name is None:
                raise OperationError("similarity_multipart_name_required")
            aggregate = multipart_models.create_composition_in_transaction(
                session,
                actor,
                name=request.name,
                collection_id=request.collection_id,
                parts=request.parts,
            )
        else:
            if request.name is not None or request.collection_id is not None:
                raise OperationError("similarity_resolution_payload_invalid")
            aggregate = multipart_models.require(
                session, actor, request.target_id, CollectionRole.EDIT
            )
            aggregate = multipart_models.append_composition_in_transaction(
                session, actor, aggregate, request.parts
            )
    except multipart_models.MultipartModelError as exc:
        raise OperationError(exc.code, kind=ErrorKind.CONFLICT) from exc
    assert aggregate.id is not None
    return aggregate.id
