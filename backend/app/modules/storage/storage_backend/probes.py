"""Filesystem capability observations shared by Vault and upload staging."""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from printstash_core.files import PublicationStrategy, publish_staged_file

from app.modules.storage.filesystem import FsKind, detect_fs_kind


class LocalRootRole(StrEnum):
    DATA = "data"
    THUMB = "thumb"
    BACKUP = "backup"
    EXTERNAL = "external"
    STAGING = "staging"


@dataclass(frozen=True)
class LocalRootProbe:
    role: LocalRootRole
    path: str
    fs_kind: FsKind
    hardlink: bool
    exclusive_create: bool
    directory_fsync: bool

    @property
    def warnings(self) -> tuple[str, ...]:
        if not self.exclusive_create and not self.hardlink:
            return (
                f"The {self.role} directory ({self.path}) cannot safely create files. Check its mount and write permissions.",
            )
        if not self.hardlink:
            return (
                f"The {self.role} filesystem ({self.path}) does not support hard links. PrintStash publishes files by copying instead, which uses more temporary space during uploads.",
            )
        return ()

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "path": self.path,
            "fs_kind": self.fs_kind,
            "hardlink": self.hardlink,
            "exclusive_create": self.exclusive_create,
            "directory_fsync": self.directory_fsync,
        }


def probe_local_root(role: LocalRootRole, root: Path) -> LocalRootProbe:
    try:
        fd, source_name = tempfile.mkstemp(
            prefix=".printstash-hardlink-probe-", dir=root
        )
    except OSError:
        return LocalRootProbe(
            role=role,
            path=str(root),
            fs_kind=detect_fs_kind(root),
            hardlink=False,
            exclusive_create=False,
            directory_fsync=False,
        )
    os.close(fd)
    source = Path(source_name)
    target = source.with_name(f"{source.name}.link")
    hardlink = False
    try:
        publish_staged_file(source, target, strategy=PublicationStrategy.LINK)
        hardlink = True
    except OSError:
        pass
    finally:
        target.unlink(missing_ok=True)
        source.unlink(missing_ok=True)

    exclusive_create = False
    probe = root / f".printstash-exclusive-probe-{uuid.uuid4().hex}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        exclusive_create = True
    except OSError:
        pass
    finally:
        probe.unlink(missing_ok=True)

    directory_fsync = False
    try:
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
            directory_fsync = True
        finally:
            os.close(directory_fd)
    except OSError:
        pass
    return LocalRootProbe(
        role=role,
        path=str(root),
        fs_kind=detect_fs_kind(root),
        hardlink=hardlink,
        exclusive_create=exclusive_create,
        directory_fsync=directory_fsync,
    )
