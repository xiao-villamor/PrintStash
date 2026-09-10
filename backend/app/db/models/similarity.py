"""Disposable geometry evidence and durable human review, without Family tables."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel


class GeometryFingerprint(SQLModel, table=True):
    __tablename__ = "geometry_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "component_index",
            "algorithm_version",
            "source_sha256",
            name="uq_geometry_fingerprint_input",
        ),
        CheckConstraint(
            "component_index >= 0 AND instance_count >= 1", name="component_values"
        ),
        CheckConstraint(
            "state IN ('pending','ready','partial','unsupported','failed')",
            name="state_values",
        ),
        Index("ix_geometry_fingerprint_claim", "state", "lease_expires_at"),
        Index(
            "ix_geometry_fingerprint_strict",
            "algorithm_version",
            "state",
            "area_volume_ratio",
        ),
        Index(
            "ix_geometry_fingerprint_repair",
            "algorithm_version",
            "state",
            "normalized_area",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", ondelete="CASCADE", index=True)
    component_index: int = Field(default=0)
    algorithm_version: str = Field(max_length=64)
    source_sha256: str = Field(max_length=64)
    state: str = Field(default="pending", max_length=16)
    physical_hash_0: str | None = Field(default=None, max_length=64, index=True)
    physical_hash_1: str | None = Field(default=None, max_length=64, index=True)
    physical_hash_2: str | None = Field(default=None, max_length=64, index=True)
    physical_hash_3: str | None = Field(default=None, max_length=64, index=True)
    normalized_hash_0: str | None = Field(default=None, max_length=64, index=True)
    normalized_hash_1: str | None = Field(default=None, max_length=64, index=True)
    normalized_hash_2: str | None = Field(default=None, max_length=64, index=True)
    normalized_hash_3: str | None = Field(default=None, max_length=64, index=True)
    vertex_count: int | None = None
    face_count: int | None = None
    component_count: int | None = None
    euler_characteristic: int | None = None
    watertight: bool | None = None
    surface_area: float | None = None
    volume: float | None = None
    area_volume_ratio: float | None = None
    normalized_area: float | None = None
    hull_ratio: float | None = None
    fill_ratio: float | None = None
    eigen_ratio_0: float | None = Field(default=None, index=True)
    inertia_ratio_0: float | None = None
    inertia_ratio_1: float | None = None
    radius: float | None = None
    d2_blob: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )
    sh_blob: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )
    view_blob: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )
    instance_count: int = Field(default=1)
    recipe_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    metrics_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    unavailable_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    instances_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    failure_code: str | None = Field(default=None, max_length=64)
    attempts: int = Field(default=0)
    lease_token: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = None
    duration_ms: int | None = None
    peak_rss_bytes: int | None = Field(default=None, sa_type=BigInteger)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SimilarityRun(SQLModel, table=True):
    __tablename__ = "similarity_runs"
    __table_args__ = (
        UniqueConstraint("active_scope_key", name="uq_similarity_run_active_scope"),
        CheckConstraint(
            "state IN ('queued','running','cancelling','cancelled','completed','failed')",
            name="state_values",
        ),
        Index("ix_similarity_run_claim", "state", "lease_expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    actor_id: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL", index=True
    )
    scope: str = Field(max_length=32)
    scope_ids_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    active_scope_key: str | None = Field(default=None, max_length=64)
    algorithm_version: str = Field(max_length=64)
    state: str = Field(default="queued", max_length=16)
    phase: str = Field(default="fingerprint", max_length=16)
    checkpoint_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    settings_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    counters_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    cutoff: datetime = Field(default_factory=utcnow)
    cancel_requested: bool = Field(default=False)
    lease_token: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=64)
    trigger: str = Field(default="manual", max_length=16)
    peak_rss_bytes: int | None = Field(default=None, sa_type=BigInteger)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_activity_at: datetime = Field(default_factory=utcnow)


class SimilarityCandidate(SQLModel, table=True):
    __tablename__ = "similarity_candidates"
    __table_args__ = (
        UniqueConstraint(
            "model_a_id",
            "model_b_id",
            "algorithm_version",
            name="uq_similarity_candidate_pair",
        ),
        CheckConstraint("model_a_id < model_b_id", name="ordered_pair"),
        CheckConstraint(
            "review_state IN ('open','confirmed','rejected','later')",
            name="review_state_values",
        ),
        CheckConstraint("freshness IN ('current','stale')", name="freshness_values"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index(
            "ix_similarity_candidate_review",
            "review_state",
            "freshness",
            "confidence",
            "id",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    model_a_id: int = Field(foreign_key="models.id", ondelete="CASCADE", index=True)
    model_b_id: int = Field(foreign_key="models.id", ondelete="CASCADE", index=True)
    algorithm_version: str = Field(max_length=64)
    run_id: int | None = Field(
        default=None, foreign_key="similarity_runs.id", ondelete="SET NULL", index=True
    )
    evidence_class: str = Field(max_length=32, index=True)
    confidence: float = Field(default=0)
    exact_equivalence: bool = Field(default=False)
    primary_lineage_key: str | None = Field(default=None, max_length=64)
    summary_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    review_state: str = Field(default="open", max_length=16)
    resolution_kind: str | None = Field(default=None, max_length=32)
    freshness: str = Field(default="current", max_length=16)
    stale_reason: str | None = Field(default=None, max_length=64)
    reviewer_id: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL"
    )
    reconsidered_candidate_id: int | None = Field(
        default=None, foreign_key="similarity_candidates.id", ondelete="SET NULL"
    )
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    reviewed_at: datetime | None = None


class SimilarityCandidateObservation(SQLModel, table=True):
    __tablename__ = "similarity_candidate_observations"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "lineage_key", name="uq_similarity_observation_lineage"
        ),
        CheckConstraint(
            "multiplicity_a >= 1 AND multiplicity_b >= 1", name="multiplicity_values"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    candidate_id: int = Field(
        foreign_key="similarity_candidates.id", ondelete="CASCADE", index=True
    )
    fingerprint_a_id: int | None = Field(
        default=None,
        foreign_key="geometry_fingerprints.id",
        ondelete="SET NULL",
        index=True,
    )
    fingerprint_b_id: int | None = Field(
        default=None,
        foreign_key="geometry_fingerprints.id",
        ondelete="SET NULL",
        index=True,
    )
    lineage_key: str = Field(max_length=64)
    input_hash_a: str = Field(max_length=64)
    input_hash_b: str = Field(max_length=64)
    kind: str = Field(default="whole", max_length=16)
    contained_side: str | None = Field(default=None, max_length=1)
    multiplicity_a: int = Field(default=1)
    multiplicity_b: int = Field(default=1)
    evidence_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SimilarityReviewDecision(SQLModel, table=True):
    __tablename__ = "similarity_review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "actor_id", "request_id", name="uq_similarity_decision_request"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    candidate_id: int | None = Field(
        default=None,
        foreign_key="similarity_candidates.id",
        ondelete="SET NULL",
        index=True,
    )
    actor_id: int | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL", index=True
    )
    request_id: str = Field(max_length=64)
    action: str = Field(max_length=32)
    resolution_kind: str | None = Field(default=None, max_length=32)
    target_id: int | None = None
    before_state: str = Field(max_length=16)
    after_state: str = Field(max_length=16)
    candidate_version: int
    snapshot_json: str = Field(sa_column=Column(Text, nullable=False))
    request_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
