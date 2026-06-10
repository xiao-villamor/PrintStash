"""Soft-delete query scopes — the single definition of "live" vs "trashed" rows.

Every list/lookup query that must exclude soft-deleted rows goes through
``live()``; trash views go through ``trashed()``. The predicate exists in
exactly one place so the invariant is testable once and a forgotten filter
is greppable.
"""

from __future__ import annotations

from typing import Any


def live(model: Any):
    """SQL predicate: rows that are not soft-deleted."""
    return model.deleted_at.is_(None)


def trashed(model: Any):
    """SQL predicate: rows sitting in the trash."""
    return model.deleted_at.is_not(None)
