"""Artifact derivatives and administrators' requests to regenerate them."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field

from app.core.time import utcnow
from app.db.enum_columns import EnumText, enum_check

from .base import SQLModel
from .types import DerivativeKind, DerivativeState, JobKind


class ArtifactDerivative(SQLModel, table=True):
    """The lifecycle record of one derived output of one Artifact.

    A derivative is a pure function of an Artifact's bytes and a versioned
    recipe. Its *output* stays with the owner of that output (``metadata``,
    ``File.thumbnail_path``, a toolpath blob); this row records whether the
    output exists at the current recipe, how many attempts it took, and why it
    failed. An Artifact with no row for an applicable kind at the current recipe
    is pending: the derivative source finds it with an anti-join, so ingestion
    never needs to know which kinds exist.
    """

    __tablename__ = "artifact_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "kind",
            "recipe_version",
            name="uq_artifact_derivatives_recipe",
        ),
        Index(
            "ix_artifact_derivatives_kind_recipe_state",
            "kind",
            "recipe_version",
            "state",
            "next_attempt_at",
        ),
        enum_check("kind", DerivativeKind),
        enum_check("state", DerivativeState),
        CheckConstraint(
            "(state = 'running') = (attempt_token IS NOT NULL)",
            name="attempt_token_iff_running",
        ),
        # A failed or skipped attempt says why, and nothing else carries a
        # reason; only a failure waits for a retry.
        CheckConstraint(
            "(state IN ('failed', 'skipped')) = (failure_reason IS NOT NULL)",
            name="reason_iff_failed_or_skipped",
        ),
        CheckConstraint(
            "next_attempt_at IS NULL OR state = 'failed'",
            name="retry_only_when_failed",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    kind: DerivativeKind = Field(
        sa_column=Column(EnumText(DerivativeKind), nullable=False)
    )
    recipe_version: int
    state: DerivativeState = Field(
        default=DerivativeState.QUEUED,
        sa_column=Column(EnumText(DerivativeState), nullable=False),
    )
    # Only the execution that captured this token may finish a running attempt.
    attempt_token: Optional[str] = Field(default=None, max_length=36)
    attempts: int = Field(default=0)
    next_attempt_at: Optional[datetime] = None
    # Why the last attempt failed or was skipped: a code, or a sanitized error.
    failure_reason: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # The storage object this derivative published, when it publishes one.
    # A column rather than JSON because trash, backup ownership and vault
    # migration must find (and remap) every derivative object by key.
    storage_key: Optional[str] = Field(default=None, max_length=2048)
    # Owner-defined description of the output (a size, a strategy). Never
    # input: a re-derivation reads the Artifact, not this.
    output_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    duration_ms: Optional[int] = None
    # Bytes: a native render's resident set passes 2 GiB, beyond INTEGER.
    peak_rss_bytes: Optional[int] = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DerivativeRegeneration(SQLModel, table=True):
    """An administrator's "regenerate all" for one derivative kind.

    A recipe bump is the code saying outputs changed. This is an operator
    saying so without a code change (for example after changing the thumbnail
    width): every output of the kind older than ``requested_at`` counts as
    stale to the derivative source until it is re-derived.
    """

    __tablename__ = "derivative_regenerations"
    __table_args__ = (enum_check("kind", DerivativeKind),)

    kind: DerivativeKind = Field(
        sa_column=Column(EnumText(DerivativeKind), primary_key=True, nullable=False)
    )
    requested_at: datetime = Field(default_factory=utcnow)
    requested_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )


class DerivativeGroupRegeneration(SQLModel, table=True):
    """A regenerate-all request limited to an enabled producer group.

    Legacy kind-wide requests remain readable, but new requests must not
    invalidate the same kind of a group the operator has disabled.
    """

    __tablename__ = "derivative_group_regenerations"
    __table_args__ = (
        enum_check("definition", JobKind),
        enum_check("kind", DerivativeKind),
    )

    definition: JobKind = Field(
        sa_column=Column(EnumText(JobKind), primary_key=True, nullable=False)
    )
    kind: DerivativeKind = Field(
        sa_column=Column(EnumText(DerivativeKind), primary_key=True, nullable=False)
    )
    requested_at: datetime = Field(default_factory=utcnow)
    requested_by: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
    )


class MeshFingerprintContinuation(SQLModel, table=True):
    """Pending mesh analysis after independently committed basic outputs.

    Presence is durable intent. The derivative's READY row stays a terminal
    output; this required source snapshot belongs to execution input instead.
    Completion removes the exact token in the fingerprint cache transaction.
    """

    __tablename__ = "mesh_fingerprint_continuations"
    __table_args__ = (
        Index("ix_mesh_fingerprint_continuations_available", "available_at"),
        CheckConstraint(
            "(job_id IS NULL AND execution_epoch IS NULL AND job_attempt IS NULL) OR "
            "(job_id IS NOT NULL AND execution_epoch IS NOT NULL AND job_attempt IS NOT NULL "
            "AND length(execution_epoch) > 0 AND job_attempt > 0)",
            name="execution_identity",
        ),
        CheckConstraint("length(token) = 36", name="continuation_token_length"),
        CheckConstraint("length(source_sha256) = 64", name="continuation_source_hash"),
        CheckConstraint("metadata_recipe > 0", name="continuation_recipe_positive"),
        CheckConstraint("attempts > 0", name="attempts_positive"),
        CheckConstraint(
            "triangle_cap BETWEEN 100 AND 2000000", name="continuation_face_cap"
        ),
        CheckConstraint(
            "length(algorithm_version) BETWEEN 1 AND 128", name="continuation_algorithm"
        ),
        CheckConstraint(
            "length(source_identity_json) > 0", name="continuation_source_identity"
        ),
    )

    file_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    token: str = Field(max_length=36)
    # Standalone invocations have no Job authority; all three fields are null.
    # Historical execution snapshot, retained when terminal Jobs are pruned.
    job_id: str | None = Field(sa_column=Column(String(64), nullable=True))
    execution_epoch: str | None = Field(max_length=64)
    job_attempt: int | None
    attempts: int = Field(default=1)
    available_at: datetime = Field(default_factory=utcnow)
    source_sha256: str = Field(max_length=64)
    metadata_recipe: int
    algorithm_version: str = Field(max_length=128)
    triangle_cap: int
    source_identity_json: str = Field(sa_column=Column(Text, nullable=False))
    # Nullable has one meaning: no regeneration request existed for this input.
    regenerated_at: datetime | None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
