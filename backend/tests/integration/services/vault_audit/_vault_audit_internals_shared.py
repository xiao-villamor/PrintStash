"""Unit coverage for app/services/vault_audit.py's run lifecycle, per-phase
checks, and finding repair dispatch — beyond the happy-path in test_vault_audit.py."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    Collection,
    Document,
    DocumentKind,
    ExternalLibrary,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    Model,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.services import vault_audit
from app.services.storage_backend import get_backend
from app.services.storage_utils import (
    OwnedBlob,
    StorageOwnershipSnapshot,
    all_owned_blob_keys,
    ownership_snapshot,
)


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = User(username=username, hashed_password="x", is_superuser=admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_run(
    session: Session, user: User, mode: VaultAuditMode = VaultAuditMode.QUICK
) -> VaultAuditRun:
    run = VaultAuditRun(requested_by=user.id, mode=mode)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _make_model(session: Session, slug: str) -> Model:
    model = Model(name=slug, slug=slug, hash=(slug * 8)[:64].ljust(64, "0"))
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _make_file(session: Session, model: Model, **overrides) -> File:
    defaults = dict(
        model_id=model.id,
        path=f"{model.slug}.stl",
        original_filename=f"{model.slug}.stl",
        file_type=FileType.STL,
        size_bytes=16,
        sha256="a" * 64,
    )
    defaults.update(overrides)
    row = File(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# list_runs / latest_run / request_cancel / reconcile_interrupted_runs
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_primary
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_database
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_external
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_background_jobs
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# execute_run — cancellation, exceptions, embedded/unowned discovery, backups
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ignore_finding / repair_finding
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _details — malformed JSON tolerance
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_database — thumbnail existence-check exception and unreadable image
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_external — stat() raising OSError
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _check_backups — cancellation mid-loop
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# execute_run — cancellation after external check, after database check,
# after backup check, and embedded-image-missing detection
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _reparse_metadata
# --------------------------------------------------------------------------- #


class _StubStrategy:
    def process(self, _path):
        return {"material_type": "PLA", "not_a_real_field": "ignored"}, b""


# --------------------------------------------------------------------------- #
# ownership_snapshot — id-less rows and id-mismatched embedded image refs
# --------------------------------------------------------------------------- #


def _patch_exec_injecting_unpersisted_row(
    monkeypatch, db_session, entity_type, extra_row
):
    """Wrap ``session.exec`` so a query for ``entity_type`` also yields
    ``extra_row`` (an in-memory instance with ``id=None`` that was never
    flushed to the DB) alongside the real, persisted rows."""
    original_exec = db_session.exec

    class _ResultWrapper:
        def __init__(self, real_result):
            self._real_result = real_result

        def all(self):
            return [*self._real_result.all(), extra_row]

    def patched_exec(statement, *args, **kwargs):
        real_result = original_exec(statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", None)
        if descriptions and descriptions[0].get("type") is entity_type:
            return _ResultWrapper(real_result)
        return real_result

    monkeypatch.setattr(db_session, "exec", patched_exec)


__all__ = [
    "BackgroundJob",
    "Collection",
    "Document",
    "DocumentKind",
    "ExternalLibrary",
    "File",
    "FileType",
    "InboxItem",
    "InboxItemState",
    "OwnedBlob",
    "Session",
    "StorageOwnershipSnapshot",
    "VaultAuditFinding",
    "VaultAuditFindingState",
    "VaultAuditMode",
    "VaultAuditRun",
    "VaultAuditRunState",
    "VaultAuditSeverity",
    "_StubStrategy",
    "_make_file",
    "_make_model",
    "_make_run",
    "_make_user",
    "_patch_exec_injecting_unpersisted_row",
    "all_owned_blob_keys",
    "get_backend",
    "json",
    "ownership_snapshot",
    "pytest",
    "timedelta",
    "utcnow",
    "vault_audit",
]
