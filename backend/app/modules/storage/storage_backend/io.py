"""Crash-safe local stream publication and directory synchronization."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from printstash_core.files import publish_staged_file

from app.core.logging import get_logger

from .contracts import StorageCollisionError

logger = get_logger(__name__)


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes after create/rename/unlink."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_stream_create_only(src: BinaryIO, dest: Path) -> Path:
    """Fully stage and sync a stream, then publish *dest* without replacement.

    Hardlinkless destinations use an exclusive copy; consume only after return.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".printstash-download-", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as destination:
            shutil.copyfileobj(src, destination)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            publish_staged_file(temp, dest)
        except FileExistsError as exc:
            raise StorageCollisionError(str(dest)) from exc
        _fsync_directory(dest.parent)
        return dest
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "storage download temp cleanup failed", extra={"path": str(temp)}
            )
