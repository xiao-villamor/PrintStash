"""Product-neutral ZIP inspection and safe extraction policies."""

from __future__ import annotations

import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from .storage import stream_to_path


class ArchivePolicyError(ValueError):
    """A stable archive validation failure suitable for adapter translation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArchiveLimits:
    """Resource and path limits applied while inspecting an archive."""

    max_entries: int
    max_entry_bytes: int
    max_total_bytes: int
    max_central_directory_bytes: int
    max_path_bytes: int
    max_depth: int


@dataclass
class ArchiveEntry:
    """One selectable importable file or preview image in an archive."""

    entry_id: str
    name: str
    size_bytes: int
    file_type: str | None
    is_image: bool


def safe_entry_name(name: str) -> bool:
    """Reject absolute paths, drive letters, directories, and traversal."""
    if not name or name.endswith(("/", "\\")):
        return False
    if name.startswith(("/", "\\")):
        return False
    if len(name) > 2 and name[1] == ":":
        return False
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def safe_subdir(relative_name: str) -> str:
    """Return the validated POSIX directory part, or an empty root path."""
    parent = PurePosixPath(relative_name.replace("\\", "/")).parent
    return "" if str(parent) in (".", "") else str(parent)


def inspect_archive(
    path: Path,
    *,
    limits: ArchiveLimits,
    file_types: Mapping[str, str],
    image_suffixes: Set[str],
) -> list[ArchiveEntry]:
    """List supported entries while enforcing ZIP bomb and path policies."""
    entries: list[ArchiveEntry] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise ArchivePolicyError("archive_too_many_entries")
            central_size = max(path.stat().st_size - int(archive.start_dir), 0)
            if central_size > limits.max_central_directory_bytes:
                raise ArchivePolicyError("archive_too_large")

            total = 0
            normalized_names: set[str] = set()
            for index, info in enumerate(infos):
                normalized = unicodedata.normalize(
                    "NFC", info.filename.replace("\\", "/")
                )
                if len(normalized.encode("utf-8")) > limits.max_path_bytes:
                    raise ArchivePolicyError("archive_path_too_deep")
                if len(PurePosixPath(normalized).parts) > limits.max_depth + 1:
                    raise ArchivePolicyError("archive_path_too_deep")
                folded = normalized.casefold()
                if folded in normalized_names:
                    raise ArchivePolicyError("archive_duplicate_entry")
                normalized_names.add(folded)

                if not info.is_dir() and not safe_entry_name(info.filename):
                    raise ArchivePolicyError("archive_unsafe_entry")
                if info.is_dir():
                    continue
                if info.file_size > limits.max_entry_bytes:
                    raise ArchivePolicyError("archive_entry_too_large")
                total += info.file_size
                if total > limits.max_total_bytes:
                    raise ArchivePolicyError("archive_too_large")

                suffix = Path(info.filename).suffix.lower()
                file_type = file_types.get(suffix)
                is_image = suffix in image_suffixes
                if file_type is None and not is_image:
                    continue
                entries.append(
                    ArchiveEntry(
                        entry_id=f"{index}:{info.CRC:08x}:{info.file_size}",
                        name=info.filename,
                        size_bytes=info.file_size,
                        file_type=file_type,
                        is_image=is_image,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise ArchivePolicyError("archive_invalid") from exc
    return entries


def extract_selected(
    path: Path,
    names: list[str],
    *,
    staging_dir: Path,
    max_entry_bytes: int,
    importable_suffixes: Set[str],
    name_factory: Callable[[str], str] | None = None,
) -> list[tuple[Path, str]]:
    """Safely extract selected supported entries into a staging directory."""
    wanted = set(names)
    extracted: list[tuple[Path, str]] = []

    def default_name(suffix: str) -> str:
        return f"{uuid.uuid4().hex}{suffix}"

    make_name: Callable[[str], str] = name_factory or default_name
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.filename not in wanted or info.is_dir():
                    continue
                if not safe_entry_name(info.filename):
                    raise ArchivePolicyError("archive_unsafe_entry")
                if info.file_size > max_entry_bytes:
                    raise ArchivePolicyError("archive_entry_too_large")
                suffix = Path(info.filename).suffix.lower()
                if suffix not in importable_suffixes:
                    continue
                staged = staging_dir / make_name(suffix)
                with archive.open(info) as source:
                    stream_to_path(
                        cast(BinaryIO, source), staged, max_bytes=max_entry_bytes
                    )
                extracted.append((staged, info.filename.replace("\\", "/")))
    except Exception:
        for staged, _name in extracted:
            staged.unlink(missing_ok=True)
        raise
    return extracted
