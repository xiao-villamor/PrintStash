"""Versioned sparse work and weighted postings, separate from original text."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel


class SearchExpansion(SQLModel, table=True):
    __tablename__ = "search_expansions"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (
        CheckConstraint("phase IN ('running', 'ready', 'failed')", name="phase"),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="attempts"),
        Index("ix_search_expansions_work", "phase", "retry_at", "lease_until"),
    )
    passage_id: int = Field(
        foreign_key="search_passages.id", ondelete="CASCADE", primary_key=True
    )
    input_hash: str = Field(max_length=64)
    recipe: str = Field(max_length=64)
    phase: str = Field(default="running", max_length=16)
    attempts: int = 0
    token: str | None = Field(default=None, max_length=64)
    lease_until: datetime | None = None
    retry_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=80)
    truncated: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class SearchExpansionTerm(SQLModel, table=True):
    __tablename__ = "search_expansion_terms"
    __audit_exclude__: ClassVar[bool] = True
    __table_args__ = (CheckConstraint("weight > 0 AND weight <= 10", name="weight"),)
    passage_id: int = Field(
        foreign_key="search_expansions.passage_id", ondelete="CASCADE", primary_key=True
    )
    term: str = Field(max_length=128, primary_key=True, index=True)
    weight: float
