"""Bounded thumbnail geometry packets; no native imports in the supervisor."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .compute_geometry_wire import MAX_GEOMETRY_BYTES, body_size, descriptor

if TYPE_CHECKING:
    from .gpu_normals import RenderInput

MAX_PACKET_BYTES = MAX_GEOMETRY_BYTES + 4100
PACKET_NAME = "geometry.bin"


@dataclass(frozen=True)
class PacketDescriptor:
    size_bytes: int
    sha256: str
    face_count: int

    def __post_init__(self) -> None:
        if (
            type(self.size_bytes) is not int
            or not 4 < self.size_bytes <= MAX_PACKET_BYTES
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
            or type(self.face_count) is not int
            or not 0 < self.face_count <= 2_000_000
        ):
            raise ValueError("invalid_thumbnail_packet_descriptor")


def validate_payload(payload: bytes, width: int, height: int) -> int:
    if type(payload) is not bytes or not 4 < len(payload) <= MAX_PACKET_BYTES:
        raise ValueError("invalid_thumbnail_packet_size")
    size = struct.unpack_from("!I", payload)[0]
    if not 0 < size <= 4096 or 4 + size >= len(payload):
        raise ValueError("invalid_thumbnail_packet_header")
    header, layout = descriptor(payload[4 : 4 + size])
    if (
        header["width"] != width
        or header["height"] != height
        or header["views"] != [None]
        or header["matte"] is not False
        or body_size(layout) != len(payload) - 4 - size
    ):
        raise ValueError("invalid_thumbnail_packet_layout")
    return layout.counts[1]


def write_packet(
    prepared: RenderInput, directory: Path, width: int, height: int
) -> PacketDescriptor:
    # Called only inside the admitted disposable parser.
    from .compute_geometry import encode

    payload = encode(prepared, width, height, (None,), False)
    faces = validate_payload(payload, width, height)
    path = directory / PACKET_NAME
    descriptor_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor_fd, "wb") as stream:
        stream.write(payload)
    return PacketDescriptor(len(payload), hashlib.sha256(payload).hexdigest(), faces)


def read_packet(
    directory: Path,
    expected: PacketDescriptor,
    width: int,
    height: int,
    *,
    directory_identity: tuple[int, int],
) -> bytes:
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        owned = os.fstat(directory_fd)
        if (owned.st_dev, owned.st_ino) != directory_identity:
            raise ValueError("thumbnail_packet_directory_changed")
        fd = os.open(
            PACKET_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_size != expected.size_bytes
            ):
                raise ValueError("invalid_thumbnail_packet_file")
            payload = stream.read(expected.size_bytes + 1)
            after = os.fstat(stream.fileno())
            stable_fields = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_uid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if (
                any(
                    getattr(before, name) != getattr(after, name)
                    for name in stable_fields
                )
                or len(payload) != expected.size_bytes
            ):
                raise ValueError("thumbnail_packet_changed")
    finally:
        os.close(directory_fd)
    if hashlib.sha256(payload).hexdigest() != expected.sha256:
        raise ValueError("thumbnail_packet_digest")
    if validate_payload(payload, width, height) != expected.face_count:
        raise ValueError("thumbnail_packet_faces")
    return payload
