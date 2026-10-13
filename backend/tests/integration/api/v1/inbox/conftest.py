"""Shared setup for the `/inbox` endpoint groups.

Capture-upload slots carry storage receipts and staging leases that outlive the row wipe
between tests, so the autouse fixture here clears them; without it a reused inbox id
inherits a previous test's lease and the failure lands somewhere unrelated.

`no_egress` is the one stand-in every capture test needs: `create` validates the source
URL by resolving it, which is a real network call. Patching that single boundary is what
keeps these integration tests — everything else, including the background resolve
scheduling, stays real.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.models import (
    CaptureUploadSlot,
    InboxItem,
    InboxItemState,
    Job,
    JobKind,
    JobState,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion import inbox, scratch_windows
from app.schemas.inbox import CaptureUploadSlotsCreate

CANONICAL_URL = "https://makerworld.com/en/models/1234-widget"


@pytest.fixture(autouse=True)
def _isolate_capture_slot_lifecycle_rows(db_session: Session) -> None:
    """The shared SQLite reset predates capture slots; avoid reused inbox ids."""
    db_session.exec(delete(StorageDeleteIntent))
    db_session.exec(delete(StagingLease))
    db_session.exec(delete(CaptureUploadSlot))
    db_session.commit()


@pytest.fixture
def no_egress(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stop the source URL being resolved, and record what resolve ran for.

    Resolving is the ``ingestion.inbox_resolve`` Job's step, so the list fills when a test
    drains the engine. The stand-in moves the item on to review exactly as a
    real resolve does, or the resolve source would keep reporting it.
    """
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)
    resolved: list[int] = []

    async def fake_resolve(
        item_id: int,
        *,
        owner: scratch_windows.JobWindowOwner,
        session_factory: SessionFactory,
    ) -> None:
        assert session_factory is get_session_factory()
        with session_factory.scoped_session() as session:
            job = session.get(Job, owner.job_id)
            assert job is not None
            assert job.subject_key == f"inbox_item/{item_id}"
            assert job.execution_epoch == owner.execution_epoch
            assert job.state == JobState.RUNNING
            resolved.append(item_id)
            row = session.get(InboxItem, item_id)
            if row is not None and row.state == InboxItemState.CAPTURED:
                row.state = InboxItemState.REVIEW
                session.add(row)
                session.commit()

    monkeypatch.setattr(inbox, "resolve", fake_resolve)
    return resolved


@pytest.fixture
def queued_imports() -> Callable[[], list[tuple[int, list[str]]]]:
    """What the router queued: each queued import Job's item and its selection.

    Nothing drains the engine here, so an accepted import stays queued and the
    selection it will run is the one recorded on the item.
    """

    def read() -> list[tuple[int, list[str]]]:
        with get_session_factory().scoped_session() as session:
            rows = session.exec(
                select(InboxItem)
                .join(Job, col(Job.id) == col(InboxItem.job_id))
                .where(
                    col(Job.kind) == JobKind.INGESTION_INBOX_IMPORT,
                    col(Job.state) == JobState.QUEUED,
                )
                .order_by(col(InboxItem.id))
            ).all()
            return [
                (row.id, inbox.selected_ids(row.manifest_json))
                for row in rows
                if row.id is not None
            ]

    return read


def capture_source(
    *, provider: str = "makerworld", canonical_url: str = CANONICAL_URL
) -> dict[str, Any]:
    """A well-formed browser-extension provenance block."""
    return {
        "provider": provider,
        "canonical_url": canonical_url,
        "source_item_id": "1234",
        "source_revision": None,
        "adapter_version": "extension-v1",
        "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
        "tags": [],
    }


def slot_payload(data: bytes = b"slot-owned") -> CaptureUploadSlotsCreate:
    """A one-file capture whose slot expects exactly `data`."""
    return CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": CANONICAL_URL,
            "capture_source": capture_source(),
            "files": [
                {
                    "id": "widget.3mf",
                    "filename": "widget.3mf",
                    "media_type": "application/octet-stream",
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        }
    )


@pytest.fixture
def make_item(db_session: Session):
    """A pending-import row in whatever state the test needs."""

    def build(owner: User, **overrides: Any) -> InboxItem:
        row = InboxItem(
            **{
                "owner_user_id": owner.id,
                "source_url": "https://example.com/model",
                "source_hostname": "example.com",
                "state": InboxItemState.CAPTURED,
                **overrides,
            }
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build
