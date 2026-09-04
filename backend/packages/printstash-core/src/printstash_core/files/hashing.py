"""Streaming SHA-256 helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file without loading it at once."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """Consume a binary stream and return its SHA-256 hex digest."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()
