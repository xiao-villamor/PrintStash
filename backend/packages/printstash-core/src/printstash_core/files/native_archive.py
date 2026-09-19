"""Typed loading boundary for native ZIP policy and extraction."""

from __future__ import annotations

import importlib
from functools import cache
from pathlib import Path
from typing import Protocol, cast


class NativeArchiveHandle(Protocol):
    def selected_entries(
        self, names: list[str], max_entry_bytes: int, importable_suffixes: set[str]
    ) -> list[tuple[int, str, str]]: ...

    def selected_entry(
        self, name: str, max_entry_bytes: int, importable_suffixes: set[str]
    ) -> tuple[int, str, str] | None: ...

    def extract_to(
        self, index: int, destination: Path, max_entry_bytes: int
    ) -> None: ...

    def close(self) -> None: ...


class ArchiveKernel(Protocol):
    NativeArchive: type[NativeArchiveHandle]

    def inspect_archive(
        self,
        path: Path,
        max_entries: int,
        max_entry_bytes: int,
        max_total_bytes: int,
        max_central_directory_bytes: int,
        max_path_bytes: int,
        max_depth: int,
        file_types: dict[str, str],
        image_suffixes: set[str],
    ) -> list[tuple[str, str, int, str | None, bool]]: ...

    def safe_entry_name(self, name: str) -> bool: ...

    def safe_subdir(self, name: str) -> str: ...


@cache
def kernel() -> ArchiveKernel:
    return cast(ArchiveKernel, importlib.import_module("printstash_mesh_native"))
