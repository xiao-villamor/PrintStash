"""Stable source observations, paged discovery and verified temporary content."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol


class LibrarySourceError(RuntimeError):
    """A remote source could not provide a complete, stable observation."""

    discovery_cursor: str | None = None


@dataclass(frozen=True)
class SourceEntry:
    key: str
    size: int
    modified_at: datetime | None = None
    etag: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class SourceContent:
    """Temporary bytes and the metadata verified for that exact read."""

    path: Path
    entry: SourceEntry


@dataclass(frozen=True)
class SourcePage:
    entries: tuple[SourceEntry, ...]
    next_cursor: str | None
    complete: bool
    metadata_ops: int
    entry_cursors: tuple[str, ...] = ()
    inventory_id: str | None = None


class LibrarySource(Protocol):
    """The only interface discovery and ArtifactContent use for remote bytes."""

    def probe(self) -> int: ...

    def list_page(
        self, prefix: str, *, cursor: str | None, limit: int
    ) -> SourcePage: ...

    @contextmanager
    def materialize(
        self,
        key: str,
        *,
        expected: SourceEntry | None = None,
        directory: Path | None = None,
    ) -> Iterator[SourceContent]: ...
