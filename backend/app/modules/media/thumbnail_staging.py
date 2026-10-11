"""Owned visual handoff after the parser exits, under prepared-byte credits."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

from app.core.cancellation import checkpoint
from app.runtime import preparation_runtime

if TYPE_CHECKING:
    from .gpu_normals import RenderInput
    from .mesh_contracts import ThumbnailResult

MAX_PACKET_BYTES = 48 * 1024**2 + 4096
PACKET_NAME = "render.geometry"
PIXELS_NAME = "render.rgba"


class FinalizationMode(StrEnum):
    ENCODE = "encode"
    RENDER = "render"


@dataclass(frozen=True)
class StagedRender:
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if type(self.size) is not int or not 4 <= self.size <= MAX_PACKET_BYTES:
            raise ValueError("staged_render_size")
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
        ):
            raise ValueError("staged_render_digest")


@dataclass(frozen=True)
class StagedThumbnailResult:
    """Measurements exist, while the requested image still needs rendering."""

    analysis: ThumbnailResult
    render: StagedRender

    def __post_init__(self) -> None:
        if self.analysis.image is not None:
            raise ValueError("staged_thumbnail_already_rendered")


def max_rgba_bytes(width: int, height: int) -> int:
    if any(type(v) is not int or not 1 <= v <= 1280 for v in (width, height)):
        raise ValueError("staged_thumbnail_dimensions")
    factor = PREVIEW_PROFILE.supersample_for(width)
    return width * height * factor**2 * 4


def output_bytes(width: int, height: int) -> int:
    """Prepared storage owns both the immutable packet and one RGBA file."""
    return MAX_PACKET_BYTES + max_rgba_bytes(width, height)


def resident_bytes(width: int, height: int) -> int:
    """Bound one packet plus IPC framing, decoded pixels and encoder input."""
    return MAX_PACKET_BYTES + 3 * max_rgba_bytes(width, height)


@dataclass(frozen=True)
class ThumbnailStaging:
    directory: Path
    width: int
    height: int
    _identity: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        max_rgba_bytes(self.width, self.height)
        object.__setattr__(self, "directory", self.directory.absolute())
        owned = self.directory.lstat()
        if not stat.S_ISDIR(owned.st_mode):
            raise ValueError("staged_thumbnail_workspace")
        object.__setattr__(self, "_identity", (owned.st_dev, owned.st_ino))

    def _check_directory(self) -> None:
        current = self.directory.lstat()
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != self._identity
        ):
            raise ValueError("staged_thumbnail_workspace_changed")

    def write(self, prepared: RenderInput) -> StagedRender:
        from .compute_geometry import encode

        self._check_directory()
        payload = encode(prepared, self.width, self.height, (None,), False)
        receipt = StagedRender(len(payload), hashlib.sha256(payload).hexdigest())
        descriptor = os.open(
            self.directory / PACKET_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            if stream.write(payload) != receipt.size:
                raise OSError("incomplete staged render write")
        return receipt

    def read(self, receipt: StagedRender) -> bytes:
        """Read only the admitted file, after clean parser exit, into owned bytes."""
        self._check_directory()
        descriptor = os.open(self.directory / PACKET_NAME, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != receipt.size:
                raise ValueError("staged_render_file")
            payload = stream.read(receipt.size)
            if stream.read(1):
                raise ValueError("staged_render_changed")
            after = os.fstat(stream.fileno())
            current = (self.directory / PACKET_NAME).lstat()
        if (
            len(payload) != receipt.size
            or hashlib.sha256(payload).hexdigest() != receipt.sha256
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("staged_render_changed")
        return payload

    def write_pixels(self, pixels: bytes) -> Path:
        self._check_directory()
        if len(pixels) != self.width * self.height * 4:
            raise ValueError("staged_thumbnail_pixels")
        path = self.directory / PIXELS_NAME
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            if stream.write(pixels) != len(pixels):
                raise OSError("incomplete staged pixels write")
        return path


@contextmanager
def reserve(directory: Path, width: int, height: int) -> Iterator[ThumbnailStaging]:
    """An extra sublimit; callers reserve resident_bytes in their source lease."""
    with preparation_runtime.visual_slot(
        resident_bytes(width, height), checkpoint=checkpoint
    ):
        path = directory / "thumbnail-staging"
        path.mkdir(mode=0o700)
        identity = path.stat()
        try:
            yield ThumbnailStaging(path, width, height)
        finally:
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError("staged_thumbnail_workspace_changed")
            shutil.rmtree(path)
