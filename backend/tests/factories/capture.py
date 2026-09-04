"""Builders for the capture pipeline: inbox items, upload slots, results.

An `InboxItem` is a durable capture request, and its `manifest_json` is not
decoration — the import path matches the manifest's `source` block against
exactly one `ModelProvenanceSource` to decide where the bytes belong. A manifest
missing `provider` or `source_item_id` makes that match find zero rows, and the
import then refuses *silently from the test's point of view*: the item lands in a
non-terminal state and nothing raises. Three tests in this suite were asserting
against that dead path for months.

So `build_inbox_item` writes a complete `source` block by default, and
`manifest_for_source` builds one that matches a provenance source it is handed.
Getting those two consistent is the single most common setup mistake in this area.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    InboxSourceKind,
    ModelProvenanceSource,
    User,
)
from tests.factories._support import nth, reject_aliases, save, unique_hash

DEFAULT_PROVIDER = "makerworld"
DEFAULT_SOURCE_URL = "https://makerworld.com/en/models/1234-widget"
DEFAULT_ITEM_ID = "1234"


def capture_source(
    *,
    provider: str = DEFAULT_PROVIDER,
    canonical_url: str = DEFAULT_SOURCE_URL,
    source_item_id: str | None = DEFAULT_ITEM_ID,
    **overrides: Any,
) -> dict[str, Any]:
    """A complete `source` block for a capture manifest.

    All three identity fields are present because all three are checked. The
    canonical URL is also bound to the provider *and* the item id by the
    canonicalizer, so changing one of the three in a test means changing the
    matching others — otherwise the request is refused before reaching the
    behaviour under test.
    """
    return {
        "provider": provider,
        "canonical_url": canonical_url,
        "source_item_id": source_item_id,
        "source_revision": None,
        "adapter_version": "extension-v1",
        "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
        "tags": [],
        **overrides,
    }


def manifest_for_source(source: ModelProvenanceSource) -> dict[str, Any]:
    """A manifest whose `source` block matches an existing provenance row.

    Use this whenever a test builds both a provenance source and an inbox item
    that must resolve to it — which is every cover-attach and every dedupe test.
    """
    return {
        "source": capture_source(
            provider=source.provider,
            canonical_url=source.canonical_url,
            source_item_id=source.source_item_id,
        )
    }


def build_inbox_item(
    session: Session,
    owner: User,
    *,
    state: InboxItemState = InboxItemState.CAPTURED,
    source_kind: InboxSourceKind = InboxSourceKind.URL,
    manifest: dict[str, Any] | None = None,
    **overrides: Any,
) -> InboxItem:
    """A pending import owned by *owner*.

    `owner_user_id` is not nullable and the whole surface is owner-scoped, so the
    user is positional rather than an override a test can forget.
    """
    overrides.setdefault("source_url", DEFAULT_SOURCE_URL)
    overrides.setdefault(
        "manifest_json",
        json.dumps(manifest if manifest is not None else {"source": capture_source()}),
    )
    return save(
        session,
        InboxItem(
            owner_user_id=owner.id,
            source_kind=source_kind,
            state=state,
            **overrides,
        ),
    )


def build_capture_slot(
    session: Session,
    item: InboxItem,
    *,
    role: str = "file",
    uploaded: bool = False,
    **overrides: Any,
) -> CaptureUploadSlot:
    """One upload slot on a capture request.

    `role="cover"` is the slot the provenance cover is published from; `"file"` is
    a model file. `uploaded=True` marks the slot as having received its bytes,
    which is the state the finalize and attach paths look for — a `PENDING` slot
    is skipped, so a test that omits this asserts against a no-op.
    """
    reject_aliases(
        overrides, {"state": "uploaded", "storage_key": "uploaded"} if uploaded else {}
    )
    index = nth("capture_slot")
    overrides.setdefault("id", f"slot-{index}")
    overrides.setdefault("filename", f"capture-{index}.3mf")
    overrides.setdefault("media_type", "application/octet-stream")
    overrides.setdefault("size_bytes", 1)
    overrides.setdefault("sha256", unique_hash("slot_sha"))
    if uploaded:
        overrides.setdefault("state", CaptureUploadSlotState.UPLOADED)
        overrides.setdefault("storage_key", f"capture/{overrides['id']}")
    return save(
        session,
        CaptureUploadSlot(inbox_item_id=item.id, role=role, **overrides),
    )


def build_inbox_result(
    session: Session,
    item: InboxItem,
    *,
    state: InboxItemResultState = InboxItemResultState.IMPORTED,
    **overrides: Any,
) -> InboxItemResult:
    """One per-selection outcome of an import.

    A partial import is several of these with mixed states, which is what makes
    the retry path retry only the failed selections.
    """
    index = nth("inbox_result")
    overrides.setdefault("result_key", f"result-{index}")
    overrides.setdefault("source_selection_id", f"selection-{index}")
    overrides.setdefault("original_filename", f"selection-{index}.3mf")
    return save(
        session,
        InboxItemResult(inbox_item_id=item.id, state=state, **overrides),
    )
