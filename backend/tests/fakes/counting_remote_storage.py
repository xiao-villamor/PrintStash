"""Remote Artifact read contract backed by a private test directory.

Only the stream interface exposes content; byte counts measure actual chunks
returned to consumers, and direct paths/native redirects are unavailable.
"""

from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.local import LocalStorageBackend


class CountingRemoteStorage(LocalStorageBackend):
    backend_name = "counting-remote"

    def __init__(self):
        super().__init__()
        # Reads remain opaque; publications use a real local conditional writer.
        self._writer = LocalStorageBackend()
        self.bytes_read = 0
        self.open_readers = 0

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        return replace(self._writer.create_stream(src, key), backend=self.backend_name)

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        return self._writer.creation_matches(replace(receipt, backend="local"))

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        return self._writer.rollback_create(replace(receipt, backend="local"))

    def direct_path(self, key: str):
        return None

    def presigned_download_url(self, key: str, filename: str):
        return None

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024):
        self.open_readers += 1
        try:
            with Path(key).open("rb") as source:
                while chunk := source.read(chunk_size):
                    self.bytes_read += len(chunk)
                    yield chunk
        finally:
            self.open_readers -= 1

    def download_to_path(self, key: str, dest: Path):
        with dest.open("wb") as output:
            for chunk in self.stream_chunks(key):
                output.write(chunk)
        return dest

    @property
    def supports_ranges(self) -> bool:
        return True

    def stream_range(self, key: str, start: int, end: int):
        with Path(key).open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise IOError("range truncated")
                self.bytes_read += len(chunk)
                remaining -= len(chunk)
                yield chunk
