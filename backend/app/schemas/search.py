"""Authorized heterogeneous search results with plain-text match evidence."""

from typing import Literal

from printstash_core.search.passages import SubjectType
from pydantic import BaseModel, Field

from app.schemas.models import ModelListItem


class SearchEvidence(BaseModel):
    leg: str = "lexical"
    field: str
    text: str
    ranges: list[tuple[int, int]] = Field(default_factory=list)


class SearchResult(BaseModel):
    subject_type: SubjectType
    subject_id: int
    name: str
    href: str
    evidence: list[SearchEvidence]
    model: ModelListItem | None = None


class SearchResponse(BaseModel):
    items: list[SearchResult]
    next_cursor: str | None = None
    legs: list[str] = Field(default_factory=lambda: ["lexical"])
    lexical_backend: Literal["fts5", "postgres_bm25", "ranked_like"]
    degraded: list[str] = Field(default_factory=list)
    semantic_ready: bool = False
    generations: list[int] = Field(default_factory=list)
    truncated: bool = False
    outcome: Literal["results", "no_results", "no_strong_matches"] = "no_results"
    leg_errors: dict[str, str] = Field(default_factory=dict)


class SearchStatus(BaseModel):
    enabled: bool
    semantic_ready: bool
    legs: list[str]
    generations: list[int]
    degraded: list[str] = Field(default_factory=list)
    backlog: bool = False
    remote_hosts: list[str] = Field(default_factory=list)
