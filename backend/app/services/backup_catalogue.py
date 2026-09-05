"""Logical backup display and exact source selection without storage side effects."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.services.backup import BackupMeta


class BackupIdentityConflictError(RuntimeError):
    """A logical backup id names archives with different or unknown digests."""


def precedence(meta: BackupMeta) -> tuple[int, str]:
    if meta.location == "local":
        return (0, meta.path)
    if meta.path.startswith("printstash-backups/"):
        return (1, meta.path)
    if meta.location.startswith("opendal:"):
        return (2, f"{meta.location}:{meta.path}")
    return (3, meta.path)


class BackupCatalogue:
    """Display deduplication never changes the exact source authorization."""

    def __init__(self, sources: Iterable[BackupMeta]):
        self._groups: dict[str, list[BackupMeta]] = {}
        for source in sources:
            self._groups.setdefault(source.id, []).append(source)
        for identity, group in self._groups.items():
            safe = self._unambiguous(group)
            self._groups[identity] = [
                replace(source, canonical=safe and rank == 0, precedence=rank)
                for rank, source in enumerate(sorted(group, key=precedence))
            ]

    @staticmethod
    def _unambiguous(group: list[BackupMeta]) -> bool:
        digests = {source.archive_sha256 for source in group}
        return len(group) == 1 or (None not in digests and len(digests) == 1)

    def sources(self) -> list[BackupMeta]:
        return sorted(
            (source for group in self._groups.values() for source in group),
            key=lambda source: source.created_at,
            reverse=True,
        )

    def backups(self) -> list[BackupMeta]:
        chosen = [
            source
            for group in self._groups.values()
            for source in (group[:1] if self._unambiguous(group) else group)
        ]
        return sorted(chosen, key=lambda source: source.created_at, reverse=True)

    def select(
        self, backup_id: str, *, source_ref: str | None = None
    ) -> BackupMeta | None:
        group = self._groups.get(backup_id, [])
        if source_ref is not None:
            return next(
                (source for source in group if source.source_ref == source_ref), None
            )
        if not group:
            return None
        if not self._unambiguous(group):
            raise BackupIdentityConflictError("backup_identity_conflict")
        return group[0]
