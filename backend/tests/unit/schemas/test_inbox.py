"""The schema is the first refusal, before any capture logic runs.

Capture payloads arrive from a browser extension, which means from a page the user
was visiting. The source URL fields are validated at the schema boundary so an
unsafe value never reaches the code that would resolve or fetch it — the earliest
possible rejection, and the one that needs no knowledge of the capture pipeline.

The second row is about not being helpful. A malformed v2 manifest must **not**
fall back to the legacy v1 parser: a lenient fallback means an attacker chooses
which parser runs by breaking the payload, and the older one has looser rules.
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


class TestCaptureSourceV2:
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
    def test_capture_source_url_fields_reject_unsafe_values(self, value: str) -> None:
        with pytest.raises(CaptureContractError):
            CaptureSource.from_dict(_source(creator_url=value))

    def test_malformed_v2_never_falls_back_to_legacy_manifest(self) -> None:
        with pytest.raises(ValidationError):
            TypeAdapter(InboxManifestRead).validate_python(
                {"schema_version": 2, "kind": "wrong"}
            )
        assert TypeAdapter(InboxManifestRead).validate_python({"kind": "archive"})
