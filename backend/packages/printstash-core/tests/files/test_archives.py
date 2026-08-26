"""Defends ``test_inspection_returns_only_supported_files_and_images`` behavior for the ``files`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from printstash_core.files import (
    ArchiveLimits,
    ArchivePolicyError,
    extract_selected,
    inspect_archive,
    safe_subdir,
)

FILE_TYPES = {".stl": "stl", ".3mf": "3mf", ".gcode": "gcode"}
IMAGES = {".png", ".jpg"}


def _limits(**overrides: int) -> ArchiveLimits:
    values = {
        "max_entries": 100,
        "max_entry_bytes": 1024,
        "max_total_bytes": 4096,
        "max_central_directory_bytes": 4096,
        "max_path_bytes": 256,
        "max_depth": 32,
    }
    values.update(overrides)
    return ArchiveLimits(**values)


def _archive(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_inspection_returns_only_supported_files_and_images(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "bundle.zip",
        {"parts/a.stl": b"a", "preview.png": b"p", "notes.txt": b"n"},
    )

    entries = inspect_archive(
        path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
    )

    assert [(entry.name, entry.file_type, entry.is_image) for entry in entries] == [
        ("parts/a.stl", "stl", False),
        ("preview.png", None, True),
    ]
    assert all(entry.entry_id.count(":") == 2 for entry in entries)


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("../escape.stl", "archive_unsafe_entry"),
        ("/absolute.stl", "archive_unsafe_entry"),
        ("C:\\escape.stl", "archive_unsafe_entry"),
    ],
)
def test_inspection_rejects_unsafe_paths(tmp_path: Path, name: str, code: str) -> None:
    path = _archive(tmp_path / "unsafe.zip", {name: b"x"})

    with pytest.raises(ArchivePolicyError, match=code):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )


def test_inspection_rejects_unicode_normalized_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Caf\N{LATIN SMALL LETTER E WITH ACUTE}.stl", b"one")
        archive.writestr("Cafe\N{COMBINING ACUTE ACCENT}.STL", b"two")

    with pytest.raises(ArchivePolicyError, match="archive_duplicate_entry"):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )


def test_inspection_enforces_entry_and_total_limits(tmp_path: Path) -> None:
    path = _archive(tmp_path / "large.zip", {"a.stl": b"12", "b.stl": b"34"})

    with pytest.raises(ArchivePolicyError, match="archive_too_many_entries"):
        inspect_archive(
            path,
            limits=_limits(max_entries=1),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )
    with pytest.raises(ArchivePolicyError, match="archive_too_large"):
        inspect_archive(
            path,
            limits=_limits(max_total_bytes=3),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )


def test_extract_selected_stages_only_supported_entries(tmp_path: Path) -> None:
    path = _archive(tmp_path / "bundle.zip", {"a.stl": b"solid", "notes.txt": b"n"})
    staging = tmp_path / "staging"

    extracted = extract_selected(
        path,
        ["a.stl", "notes.txt"],
        staging_dir=staging,
        max_entry_bytes=1024,
        importable_suffixes=set(FILE_TYPES),
        name_factory=lambda suffix: f"fixed{suffix}",
    )

    assert extracted == [(staging / "fixed.stl", "a.stl")]
    assert extracted[0][0].read_bytes() == b"solid"
    assert safe_subdir("nested\\part.stl") == "nested"


def test_invalid_zip_has_a_stable_error_code(tmp_path: Path) -> None:
    path = tmp_path / "invalid.zip"
    path.write_bytes(b"not a zip")

    with pytest.raises(ArchivePolicyError, match="archive_invalid"):
        inspect_archive(
            path, limits=_limits(), file_types=FILE_TYPES, image_suffixes=IMAGES
        )
