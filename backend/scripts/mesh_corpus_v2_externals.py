"""Explicit pinned slicer download boundary, independent of corpus orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.request import urlopen

from scripts.mesh_corpus_v2_contracts import MAX_EXTERNAL_BYTES, ExternalReference

FETCH_CHUNK_BYTES = 64 * 1024


def external_references() -> tuple[ExternalReference, ...]:
    """Reuse the historical ADR pins, with their recorded exact input lengths."""
    source = Path(__file__).resolve().parents[2] / "docs/adr/0009-3mf-pilot"
    refs = json.loads((source / "external-inputs.json").read_text())
    cases = json.loads((source / "cases.json").read_text())
    return tuple(
        ExternalReference(
            filename=f"real-{row['case'].lower()}.3mf",
            input_bytes=cases[row["case"]]["input_bytes"],
            **row,
        )
        for row in refs
    )


def _fetch(reference: ExternalReference) -> Iterable[bytes]:
    with urlopen(reference.url, timeout=30) as response:
        while chunk := response.read(FETCH_CHUNK_BYTES):
            yield chunk


def materialize_external(
    root: Path,
    reference: ExternalReference,
    *,
    fetch: Callable[[ExternalReference], Iterable[bytes]] = _fetch,
) -> Path:
    """Explicit egress boundary: bounded streaming and verification before replace."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / reference.filename
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=root, prefix=".download-", delete=False
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            total = 0
            for chunk in fetch(reference):
                if not isinstance(chunk, bytes) or len(chunk) > FETCH_CHUNK_BYTES:
                    raise ValueError("external fetch chunk exceeds boundary")
                total += len(chunk)
                if total > min(MAX_EXTERNAL_BYTES, reference.input_bytes):
                    raise ValueError("external fixture exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
            if total != reference.input_bytes or digest.hexdigest() != reference.sha256:
                raise ValueError("external fixture identity differs")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
