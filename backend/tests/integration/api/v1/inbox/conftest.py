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
from typing import Any

import pytest
from sqlalchemy import delete
from sqlmodel import Session

from app.db.models import (
    CaptureUploadSlot,
    InboxItem,
    InboxItemState,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.schemas.inbox import CaptureUploadSlotsCreate
from app.services import inbox

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
    """Stop the source URL being resolved, and record what resolve was scheduled for."""
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)
    scheduled: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        scheduled.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)
    return scheduled


@pytest.fixture
def imports_run(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, list[str]]]:
    """Record what the router scheduled instead of running a real import."""
    scheduled: list[tuple[int, list[str]]] = []

    async def fake_run_import(item_id: int, selected_ids: list[str], _factory) -> None:
        scheduled.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)
    return scheduled


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
