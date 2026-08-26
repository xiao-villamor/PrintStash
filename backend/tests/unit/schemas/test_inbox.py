"""Defends ``test_capture_source_url_fields_reject_unsafe_values`` behavior for the ``schemas`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import pytest
from printstash_core.imports import CaptureContractError, CaptureSource
from pydantic import TypeAdapter, ValidationError

from app.schemas.inbox import InboxManifestRead


def _source(**field: str) -> dict:
    fields = {"title": {"value": "Widget", "origin": "confirmed"}}
    fields.update(
        {name: {"value": value, "origin": "confirmed"} for name, value in field.items()}
    )
    return {
        "provider": "myminifactory",
        "canonical_url": "https://example.test/model",
        "source_item_id": "1",
        "source_revision": None,
        "adapter_version": "v1",
        "tags": [],
        "fields": fields,
    }


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/plain,x",
        "file:///tmp/x",
        "https://user:pass@example.test/x",
        "https://example.test/\x00x",
    ],
)
def test_capture_source_url_fields_reject_unsafe_values(value: str) -> None:
    with pytest.raises(CaptureContractError):
        CaptureSource.from_dict(_source(creator_url=value))


def test_malformed_v2_never_falls_back_to_legacy_manifest() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(InboxManifestRead).validate_python(
            {"schema_version": 2, "kind": "wrong"}
        )
    assert TypeAdapter(InboxManifestRead).validate_python({"kind": "archive"})
