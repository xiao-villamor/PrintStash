"""Streaming SHA-256 helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path, *, on_chunk: Callable[[], None] | None = None) -> str:
    """Hash a file in bounded blocks, closing its owned stream on any unwind."""
    with path.open("rb") as stream:
        return sha256_stream(stream, on_chunk=on_chunk)


def sha256_stream(
    stream: BinaryIO, *, on_chunk: Callable[[], None] | None = None
) -> str:
    """Consume a caller-owned stream with optional cooperative cancellation.

    The callback runs before each read and once after EOF, before returning the
    digest. Its failures propagate unchanged; the caller retains stream ownership.
    """
    digest = hashlib.sha256()
    while True:
        if on_chunk is not None:
            on_chunk()
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    if on_chunk is not None:
        on_chunk()
    return digest.hexdigest()
