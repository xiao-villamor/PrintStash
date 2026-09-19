"""Optional ranked read port; library browse has no search-module dependency."""

from typing import Any, Protocol

from sqlmodel import Session


class ContentSearch(Protocol):
    def model_matches(
        self, session: Session, query: str, allowed_model_ids: Any
    ) -> Any: ...


_search: ContentSearch | None = None


def bind_content_search(provider: ContentSearch | None) -> ContentSearch | None:
    global _search
    previous = _search
    _search = provider
    return previous


def ranked_model_matches(session: Session, query: str, allowed_model_ids: Any) -> Any:
    return (
        _search.model_matches(session, query, allowed_model_ids)
        if _search is not None
        else None
    )
