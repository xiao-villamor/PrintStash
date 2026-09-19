"""Durable text projections; inference and native indexes are separate derivatives."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field

from app.core.time import utcnow
from app.db.search_vector import SearchVectorExpression

from .base import SQLModel


class SearchPassage(SQLModel, table=True):
    __tablename__ = "search_passages"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "visibility_segment_key",
            "chunk_index",
            "recipe_version",
            name="uq_search_passage_identity",
        ),
        CheckConstraint(
            "subject_type IN ('model', 'collection', 'multipart_model', 'document')",
            name="subject_type",
        ),
        CheckConstraint("subject_id > 0", name="subject_id"),
        CheckConstraint("chunk_index >= 0 AND chunk_index < 16", name="chunk_index"),
        CheckConstraint("recipe_version > 0", name="recipe_version"),
        CheckConstraint("length(text) <= 16384", name="text_length"),
        Index("ix_search_passages_watermark", "updated_at", "id"),
        Index("ix_search_passages_lexemes", "lexemes", postgresql_using="gin"),
    )
    id: int | None = Field(default=None, primary_key=True)
    subject_type: str = Field(max_length=32)
    subject_id: int
    visibility_segment_key: str = Field(max_length=64)
    # Additional Subject permissions, conjunctive; owner permission is mandatory.
    access_dependencies_json: str = Field(sa_column=Column(Text, nullable=False))
    chunk_index: int
    recipe_version: int
    content_hash: str = Field(max_length=64)
    text: str = Field(sa_column=Column(Text, nullable=False))
    title: str = Field(
        default="",
        sa_column=Column(Text, nullable=False, server_default=sql_text("''")),
    )
    tags_text: str = Field(
        default="",
        sa_column=Column(Text, nullable=False, server_default=sql_text("''")),
    )
    token_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    lexemes: str | None = Field(
        default=None,
        sa_column=Column(
            TSVECTOR().with_variant(Text(), "sqlite"),
            Computed(SearchVectorExpression(), persisted=True),
        ),
    )
    truncated: bool = False
    source_updated_at: datetime
    updated_at: datetime = Field(default_factory=utcnow)


class SearchDependency(SQLModel, table=True):
    """Extraction dependencies survive removal of a source or relationship."""

    __tablename__ = "search_dependencies"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "source_kind",
            "source_id",
            name="uq_search_dependency_identity",
        ),
        Index("ix_search_dependency_source", "source_kind", "source_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    subject_type: str = Field(max_length=32)
    subject_id: int
    source_kind: str = Field(max_length=32)
    source_id: int


class SearchReconciliationState(SQLModel, table=True):
    """Independent durable change watermark and rolling partition for each kind."""

    __tablename__ = "search_reconciliation_states"
    __audit_exclude__: ClassVar[bool] = True
    id: int | None = Field(default=None, primary_key=True)
    subject_type: str = Field(max_length=32, unique=True)
    watermark_at: datetime = Field(default=datetime(1970, 1, 1))
    watermark_id: int = 0
    partition_after_id: int = 0
    orphan_after_id: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class SearchProjectionRequest(SQLModel, table=True):
    """Coalesced source change; its bounded fanout resumes after a restart.

    Projection performs database work only. The worker locks this row for one
    short page transaction, so it needs no independently expiring task lease.
    """

    __tablename__ = "search_projection_requests"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "source_kind", "source_id", name="uq_search_projection_source"
        ),
        Index("ix_search_projection_due", "next_attempt_at", "created_at"),
        CheckConstraint("source_id > 0", name="source_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    source_kind: str = Field(max_length=32)
    source_id: int
    revision: int = 1
    cursor_kind: str = Field(default="", max_length=32)
    cursor_id: int = 0
    attempts: int = 0
    next_attempt_at: datetime = Field(default_factory=utcnow)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SearchLexicalPosting(SQLModel, table=True):
    """Per-passage frequencies used by the PostgreSQL BM25 scorer."""

    __tablename__ = "search_lexical_postings"
    __audit_exclude__: ClassVar[bool] = True
    passage_id: int = Field(
        foreign_key="search_passages.id", ondelete="CASCADE", primary_key=True
    )
    term: str = Field(max_length=128, primary_key=True, index=True)
    frequency: float


class SearchLexicalTerm(SQLModel, table=True):
    __tablename__ = "search_lexical_terms"
    __audit_exclude__: ClassVar[bool] = True
    term: str = Field(max_length=128, primary_key=True)
    document_frequency: int


class SearchLexicalState(SQLModel, table=True):
    __tablename__ = "search_lexical_state"
    __audit_exclude__: ClassVar[bool] = True
    id: int = Field(default=1, primary_key=True)
    document_count: int = 0
    total_length: int = 0
    native_phase: str = Field(default="absent", max_length=32)
    native_after_id: int = 0
    failure_code: str | None = Field(default=None, max_length=64)
