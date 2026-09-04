"""Builders for where a model came from: sources, captures, links, covers.

Provenance is four tables that only mean something together, and the joins are
all on values a test has to keep consistent by hand. Two of those are worth
taking away from the caller.

`identity_key` is the unique-per-model fingerprint of a source. Two sources on
one model with the same key violate a database constraint; two with *different*
keys that are meant to be the same source silently become two sources, and every
"exactly one source matched" check downstream then refuses. It is generated here.

The cover is the one piece of this that owns bytes, and they are **private to the
instance** — reachable only through the API, never by storage key. `build_cover`
records the row; a test that needs the bytes to actually exist on the backend
should publish them through `source_covers.put`, which is the path that takes an
ownership receipt.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.db.models import (
    ArtifactProvenanceLink,
    File,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
)
from tests.factories._support import nth, save, unique_hash


def build_provenance_source(
    session: Session,
    model: Model,
    *,
    provider: str = "printables",
    source_item_id: str | None = "123456",
    canonical_url: str | None = None,
    tags: list[str] | None = None,
    **overrides: Any,
) -> ModelProvenanceSource:
    """One remote source this model was captured from.

    `source_item_id=None` is a real case rather than an omission — some providers
    return only a page slug — and it changes which lookups match, so it is spelled
    out rather than defaulted away.
    """
    index = nth("provenance_source")
    overrides.setdefault("identity_key", unique_hash("identity_key"))
    overrides.setdefault("tags_json", json.dumps(tags if tags is not None else []))
    return save(
        session,
        ModelProvenanceSource(
            model_id=model.id,
            provider=provider,
            source_item_id=source_item_id,
            canonical_url=canonical_url
            or f"https://www.printables.com/model/{source_item_id or index}",
            **overrides,
        ),
    )


def build_capture(
    session: Session,
    source: ModelProvenanceSource,
    *,
    captured_at: datetime | None = None,
    snapshot: dict[str, Any] | None = None,
    **overrides: Any,
) -> ProvenanceCapture:
    """One snapshot in a source's append-only capture history.

    `snapshot_sha256` is unique per source, so recapturing identical bytes is
    deliberately not a new row. Pass it explicitly to test that constraint.
    """
    index = nth("capture")
    overrides.setdefault("adapter_version", f"printables/{index}")
    overrides.setdefault("snapshot_json", json.dumps(snapshot or {}))
    overrides.setdefault("snapshot_sha256", unique_hash("snapshot_sha"))
    if captured_at is not None:
        overrides.setdefault("captured_at", captured_at)
    return save(
        session,
        ProvenanceCapture(provenance_source_id=source.id, **overrides),
    )


def build_artifact_link(
    session: Session,
    file: File,
    source: ModelProvenanceSource,
    **overrides: Any,
) -> ArtifactProvenanceLink:
    """Attach a source-file identity to an artifact.

    The link never owns bytes: it records *which remote file* this artifact came
    from, which is what makes a re-download deduplicate instead of duplicating.
    """
    overrides.setdefault("source_filename", file.original_filename)
    overrides.setdefault("blob_sha256", file.sha256)
    overrides.setdefault("import_key", unique_hash("import_key"))
    return save(
        session,
        ArtifactProvenanceLink(
            file_id=file.id, provenance_source_id=source.id, **overrides
        ),
    )


def build_cover(
    session: Session,
    source: ModelProvenanceSource,
    **overrides: Any,
) -> ModelSourceCover:
    """The row for a source's representative image, without publishing bytes."""
    overrides.setdefault("storage_key", f"covers/source-{source.id}.webp")
    overrides.setdefault("size_bytes", 1024)
    return save(
        session,
        ModelSourceCover(provenance_source_id=source.id, **overrides),
    )
