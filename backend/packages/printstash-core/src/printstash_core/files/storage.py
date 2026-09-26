"""Portable filesystem-layout and upload-staging helpers."""

from __future__ import annotations

import errno
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

_CHUNK_SIZE = 1024 * 1024
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class _Digest(Protocol):
    def update(self, data: bytes, /) -> object:
        """Add bytes to the digest state."""


class UploadTooLarge(Exception):
    """A streamed file exceeded its configured byte limit."""


class UnsafeStorageComponent(ValueError):
    """A user-controlled label cannot safely be used as one path component."""


def validate_leaf_name(name: str, *, max_bytes: int = 255) -> str:
    """Return a canonical safe leaf name or raise ``UnsafeStorageComponent``.

    Backslashes are rejected on POSIX too, giving portable manifests and
    archives the same path semantics on every supported host.
    """
    normalized = unicodedata.normalize("NFC", name)
    if (
        not normalized
        or normalized in {".", ".."}
        or Path(normalized).is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or "/" in normalized
        or "\\" in normalized
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
        or len(normalized.encode("utf-8")) > max_bytes
    ):
        raise UnsafeStorageComponent("unsafe_storage_component")
    return normalized


def slugify(name: str) -> str:
    """Produce a filesystem-safe, kebab-case slug."""
    normalized = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return slug or "model"


def ensure_unique_slug(base: str, exists: Callable[[str], bool]) -> str:
    """Append ``-2``, ``-3``, and so on until a slug is available."""
    candidate = base
    suffix = 2
    while exists(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def stream_to_path(
    src: BinaryIO,
    dest: Path,
    *,
    max_bytes: int | None = None,
    digest: _Digest | None = None,
) -> int:
    """Stream ``src`` to a new private staging path and return bytes written.

    A hard link publishes the completed sibling temp atomically. On filesystems
    without hard links, an exclusive copy preserves no-replace semantics but
    may expose partial bytes: callers must not consume or advertise ``dest``
    until this function returns. A failed copy leaves an uncertain destination
    for operator review; it never unlinks a possible raced replacement.
    When supplied, ``digest`` observes the input stream exactly once.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    fd, temp_name = tempfile.mkstemp(prefix=".printstash-stage-", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = src.read(_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if max_bytes is not None and bytes_written > max_bytes:
                    raise UploadTooLarge
                out.write(chunk)
                if digest is not None:
                    digest.update(chunk)
            out.flush()
            os.fsync(out.fileno())
        publish_staged_file(temp, dest)
        return bytes_written
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            # An uncertain private temp is safer than turning a successfully
            # published staging file into a reported failure.
            pass


def publish_staged_file(staged_path: Path, dest: Path) -> None:
    """Publish a closed, synced private file without replacing ``dest``.

    Keep the source for caller-owned cleanup. Prefer atomic hard-link
    publication; unsupported filesystems use an exclusive, synced copy.
    Consumers must wait for return before reading the destination: the copy
    can expose partial bytes. On failure leave uncertain destinations intact.
    Both paths must be inside application-controlled staging directories.
    """
    try:
        os.link(staged_path, dest, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in {
            errno.EXDEV,
            errno.EPERM,
            errno.EOPNOTSUPP,
            errno.EMLINK,
        }:
            raise
        # O_EXCL rejects existing files and symlinks, including dangling
        # symlinks. Never replace this with exists() followed by rename().
        out_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(out_fd, "wb") as out:
            with staged_path.open("rb") as staged:
                shutil.copyfileobj(staged, out, _CHUNK_SIZE)
            out.flush()
            os.fsync(out.fileno())
