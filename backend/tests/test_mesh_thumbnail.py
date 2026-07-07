"""Coverage for the embedded-3MF-thumbnail extractor.

3MF files are ZIP archives that slicers populate with a rendered plate preview.
Using it skips the software rasteriser — but the extractor must be picky about
where the PNG lives, validate the magic bytes, and never crash on a junk file.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.mesh_processing import _PNG_MAGIC, extract_embedded_3mf_thumbnail

_PNG_SMALL = _PNG_MAGIC + b"small"
_PNG_BIG = _PNG_MAGIC + b"x" * 500


def _make_3mf(tmp_path: Path, entries: dict[str, bytes], *, suffix: str = ".3mf") -> Path:
    path = tmp_path / f"model{suffix}"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_picks_largest_thumbnail(tmp_path: Path) -> None:
    p = _make_3mf(
        tmp_path,
        {"Metadata/thumbnail.png": _PNG_SMALL, "Metadata/plate_1.png": _PNG_BIG},
    )
    assert extract_embedded_3mf_thumbnail(p) == _PNG_BIG


@pytest.mark.parametrize("folder", ["Metadata", "3D/thumbnails", "thumbnails"])
def test_accepts_known_thumbnail_dirs(tmp_path: Path, folder: str) -> None:
    p = _make_3mf(tmp_path, {f"{folder}/preview.png": _PNG_BIG})
    assert extract_embedded_3mf_thumbnail(p) == _PNG_BIG


def test_ignores_png_outside_thumbnail_dirs(tmp_path: Path) -> None:
    p = _make_3mf(tmp_path, {"random/foo.png": _PNG_BIG, "3D/model.model": b"<xml/>"})
    assert extract_embedded_3mf_thumbnail(p) is None


def test_rejects_non_3mf_suffix(tmp_path: Path) -> None:
    p = _make_3mf(tmp_path, {"Metadata/thumbnail.png": _PNG_BIG}, suffix=".zip")
    assert extract_embedded_3mf_thumbnail(p) is None


def test_rejects_data_without_png_magic(tmp_path: Path) -> None:
    p = _make_3mf(tmp_path, {"Metadata/thumbnail.png": b"not actually a png"})
    assert extract_embedded_3mf_thumbnail(p) is None


def test_returns_none_for_corrupt_archive(tmp_path: Path) -> None:
    p = tmp_path / "broken.3mf"
    p.write_bytes(b"this is not a zip file")
    assert extract_embedded_3mf_thumbnail(p) is None


def test_returns_none_when_no_png_present(tmp_path: Path) -> None:
    p = _make_3mf(tmp_path, {"3D/model.model": b"<xml/>"})
    assert extract_embedded_3mf_thumbnail(p) is None
