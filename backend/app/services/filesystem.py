"""Shared filesystem classification for storage and library behavior."""

from __future__ import annotations

import os
from typing import Literal

FsKind = Literal["local", "network", "unknown"]

_NETWORK_FSTYPES = {
    "nfs",
    "nfs4",
    "cifs",
    "smbfs",
    "smb3",
    "afs",
    "ncpfs",
    "9p",
}
_LOCAL_FSTYPES = {
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "zfs",
    "f2fs",
    "reiserfs",
    "jfs",
    "overlay",
    "tmpfs",
}


def detect_fs_kind(path: str | os.PathLike[str]) -> FsKind:
    """Classify a Linux mount conservatively for inode/watch guarantees."""
    target = os.path.realpath(str(path))
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            entries = handle.readlines()
    except OSError:
        return "unknown"

    best_mount = ""
    best_fstype = ""
    for line in entries:
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        left = parts[0].split()
        right = parts[1].split()
        if len(left) < 5 or not right:
            continue
        mount_point = left[4]
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) >= len(best_mount):
                best_mount = mount_point
                best_fstype = right[0]

    if not best_fstype:
        return "unknown"
    base = best_fstype.split(".", 1)[0].lower()
    if base in _NETWORK_FSTYPES or "smb" in base or "cifs" in base:
        return "network"
    if base in _LOCAL_FSTYPES:
        return "local"
    return "unknown"
