"""Pure filesystem-layout helpers for the vault.

Slug generation and upload staging only — anything touching stored blobs
goes through ``get_backend()`` from ``app.services.storage_backend``
(``blob_key``, ``thumbnail_key``, ``move_in``, ``local_path``...).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import BinaryIO

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Produce a filesystem-safe, kebab-case slug."""
    normalized = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "model"


def ensure_unique_slug(base: str, exists: callable) -> str:
    """Append -2, -3, ... until exists(slug) returns False."""
    candidate = base
    n = 2
    while exists(candidate):
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def stream_to_path(src: BinaryIO, dest: Path) -> int:
    """Stream a binary source to a local path, returning bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with dest.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            bytes_written += len(chunk)
    return bytes_written
