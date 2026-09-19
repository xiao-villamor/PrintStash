"""Product-neutral ZIP inspection and safe extraction policies."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

from .native_archive import kernel


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
    return kernel().safe_entry_name(name)


def safe_subdir(relative_name: str) -> str:
    """Return the validated POSIX directory part, or an empty root path."""
    return kernel().safe_subdir(relative_name)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _policy_call(call: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
    try:
        return call(*args, **kwargs)
    except ValueError as exc:
        code = str(exc)
        if code.startswith("archive_"):
            raise ArchivePolicyError(code) from exc
        raise


def inspect_archive(
    path: Path,
    *,
    limits: ArchiveLimits,
    file_types: Mapping[str, str],
    image_suffixes: Set[str],
) -> list[ArchiveEntry]:
    """List supported entries while enforcing ZIP bomb and path policies."""
    native_entries = _policy_call(
        kernel().inspect_archive,
        path,
        limits.max_entries,
        limits.max_entry_bytes,
        limits.max_total_bytes,
        limits.max_central_directory_bytes,
        limits.max_path_bytes,
        limits.max_depth,
        dict(file_types),
        set(image_suffixes),
    )
    return [ArchiveEntry(*entry) for entry in native_entries]


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
    archive = _policy_call(kernel().NativeArchive, path)
    try:
        selected = _policy_call(
            archive.selected_entries,
            list(wanted),
            max_entry_bytes,
            set(importable_suffixes),
        )
        for index, suffix, source_name in selected:
            staged = staging_dir / make_name(suffix)
            _policy_call(archive.extract_to, index, staged, max_entry_bytes)
            extracted.append((staged, source_name))
    except Exception:
        for staged, _name in extracted:
            staged.unlink(missing_ok=True)
        raise
    finally:
        archive.close()
    return extracted


def iter_selected(
    path: Path,
    names: list[str],
    *,
    staging_dir: Path,
    max_entry_bytes: int,
    importable_suffixes: Set[str],
):
    """Yield one temporary selected entry; release it before extracting the next.

    The caller first applies package-wide inspection limits. This iterator owns
    its temporary file until the consumer moves it or advances/closes the stream.
    It retains one ZIP directory and one expanded entry, regardless of archive size.
    """
    archive = _policy_call(kernel().NativeArchive, path)
    try:
        for name in dict.fromkeys(names):
            selected = _policy_call(
                archive.selected_entry,
                name,
                max_entry_bytes,
                set(importable_suffixes),
            )
            if selected is None:
                continue
            index, suffix, source_name = selected
            staged = staging_dir / f"{uuid.uuid4().hex}{suffix}"
            try:
                _policy_call(archive.extract_to, index, staged, max_entry_bytes)
                yield staged, source_name
            finally:
                staged.unlink(missing_ok=True)
    finally:
        archive.close()
