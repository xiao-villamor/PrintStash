"""Bounded streaming transfer policy independent of providers and HTTP."""

from __future__ import annotations

import hashlib
import io
import time
from collections.abc import Callable, Iterator
from typing import BinaryIO


class ChunkReader(io.RawIOBase):
    """Adapt one provider stream without materializing a complete Artifact."""

    def __init__(self, chunks: Iterator[bytes]):
        self.chunks = chunks
        self.pending = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while not self.pending:
            try:
                self.pending = memoryview(next(self.chunks))
            except StopIteration:
                return 0
        count = min(len(buffer), len(self.pending))
        buffer[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count

    def close(self) -> None:
        close = getattr(self.chunks, "close", None)
        if close:
            close()
        super().close()


class ThrottledReader:
    def __init__(
        self,
        stream: BinaryIO,
        bytes_per_second: int | None,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        checkpoint: Callable[[], None] | None = None,
    ):
        self.stream = stream
        self.limit = bytes_per_second
        self.started = time.monotonic()
        self.transferred = 0
        self.expected_size = expected_size
        self.expected_sha256 = expected_sha256
        self.digest = hashlib.sha256()
        self.checkpoint = checkpoint

    def read(self, size: int = -1) -> bytes:
        if self.checkpoint:
            self.checkpoint()
        maximum = (
            min(1024 * 1024, max(1, self.limit // 4)) if self.limit else 1024 * 1024
        )
        size = maximum if size < 0 else min(size, maximum)
        data = self.stream.read(size)
        self.transferred += len(data)
        self.digest.update(data)
        if self.expected_size is not None and self.transferred > self.expected_size:
            raise ValueError("migration_source_size_mismatch")
        if not data and size:
            if (
                self.expected_size is not None
                and self.transferred != self.expected_size
            ):
                raise ValueError("migration_source_size_mismatch")
            if self.expected_sha256 and self.digest.hexdigest() != self.expected_sha256:
                raise ValueError("migration_source_hash_mismatch")
        if self.limit:
            delay = self.transferred / self.limit - (time.monotonic() - self.started)
            if delay > 0:
                time.sleep(delay)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        return self.stream.tell()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return self.stream.seekable()

    def __getattr__(self, name: str):
        return getattr(self.stream, name)
