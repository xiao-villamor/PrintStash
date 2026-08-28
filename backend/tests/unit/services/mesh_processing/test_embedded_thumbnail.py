"""Reusing the slicer's own plate preview instead of rendering one.

A 3MF is a ZIP archive, and slicers drop a rendered plate preview inside it. Using
that PNG is both free and often better than the software rasteriser's output — the
slicer knows the plate, the colours, and the supports. So it is preferred whenever
one is there.

Which makes this extractor a place where an archive the host did not build decides
what bytes get stored as a model's thumbnail. Three refusals follow from that:

**Only known thumbnail locations count.** A PNG anywhere in the archive would let
a crafted 3MF nominate any of its members as the preview. The extractor looks in
the directories the 3MF spec and the common slicers use, and nowhere else.

**The magic bytes are checked, not the extension.** A member named
`thumbnail.png` holding something else must never reach storage as an image —
that is a stored file whose declared type is a lie, served back to a browser.

**A declared size over the limit is refused without reading the member.** The
size comes from the ZIP central directory, so a decompression bomb is refused
before a byte of it is inflated.

Everything degrades to `None`, which means "render one instead". A corrupt
archive, a truncated member, an absent preview: all the same answer, because a
3MF that fails here is still a perfectly importable model.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.core.config import _overlay
from app.services import thumbnail
from app.services.mesh_processing import _PNG_MAGIC, extract_embedded_3mf_thumbnail

_PNG_SMALL = _PNG_MAGIC + b"small"
_PNG_BIG = _PNG_MAGIC + b"x" * 500


def _make_3mf(
    tmp_path: Path, entries: dict[str, bytes], *, suffix: str = ".3mf"
) -> Path:
    path = tmp_path / f"model{suffix}"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


class TestExtractEmbedded3mfThumbnail:
    def test_picks_largest_thumbnail(self, tmp_path: Path) -> None:
        p = _make_3mf(
            tmp_path,
            {"Metadata/thumbnail.png": _PNG_SMALL, "Metadata/plate_1.png": _PNG_BIG},
        )
        assert extract_embedded_3mf_thumbnail(p) == _PNG_BIG

    @pytest.mark.parametrize("folder", ["Metadata", "3D/thumbnails", "thumbnails"])
    def test_accepts_known_thumbnail_dirs(self, tmp_path: Path, folder: str) -> None:
        p = _make_3mf(tmp_path, {f"{folder}/preview.png": _PNG_BIG})
        assert extract_embedded_3mf_thumbnail(p) == _PNG_BIG

    def test_valid_embedded_preview_survives_larger_invalid_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from PIL import Image

        valid_buffer = io.BytesIO()
        Image.new("RGB", (4, 4), (12, 120, 220)).save(valid_buffer, format="PNG")
        valid = valid_buffer.getvalue()
        larger_invalid = _PNG_MAGIC + b"x" * 700
        oversized = b"x" * 2_000
        monkeypatch.setattr(
            "app.services.mesh_processing._MAX_3MF_THUMBNAIL_BYTES", 1_000
        )
        monkeypatch.setattr(
            "app.services.mesh_processing._MAX_3MF_THUMBNAIL_AGGREGATE_BYTES", 1_100
        )
        path = _make_3mf(
            tmp_path,
            {
                "Metadata/larger-invalid.png": larger_invalid,
                "Metadata/oversized.png": oversized,
                "Metadata/valid.png": valid,
            },
        )

        assert extract_embedded_3mf_thumbnail(path, validate_image=True) == valid

    def test_webp_conversion_honours_configured_model_preview_size(
        self, monkeypatch
    ) -> None:
        from PIL import Image

        source = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(source, format="PNG")
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)

        encoded = thumbnail.to_webp(source.getvalue())

        with Image.open(io.BytesIO(encoded)) as result:
            assert result.size == (320, 240)

    def test_ignores_png_outside_thumbnail_dirs(self, tmp_path: Path) -> None:
        p = _make_3mf(
            tmp_path, {"random/foo.png": _PNG_BIG, "3D/model.model": b"<xml/>"}
        )
        assert extract_embedded_3mf_thumbnail(p) is None

    def test_invalid_thumbnail_is_never_returned_as_raw_storage_payload(self) -> None:
        with pytest.raises(ValueError, match="thumbnail_too_large"):
            thumbnail.to_webp(b"not-an-image")

    def test_rejects_thumbnail_declared_over_limit_without_reading_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _make_3mf(tmp_path, {"Metadata/thumbnail.png": _PNG_BIG})
        monkeypatch.setattr("app.services.mesh_processing._MAX_3MF_THUMBNAIL_BYTES", 64)

        original_read = zipfile.ZipFile.read

        def _never_read(self, *args, **kwargs):
            raise AssertionError("oversized preview must not be materialized")

        monkeypatch.setattr(zipfile.ZipFile, "read", _never_read)
        try:
            assert extract_embedded_3mf_thumbnail(p) is None
        finally:
            monkeypatch.setattr(zipfile.ZipFile, "read", original_read)

    def test_rejects_non_3mf_suffix(self, tmp_path: Path) -> None:
        p = _make_3mf(tmp_path, {"Metadata/thumbnail.png": _PNG_BIG}, suffix=".zip")
        assert extract_embedded_3mf_thumbnail(p) is None

    def test_returns_none_when_no_png_present(self, tmp_path: Path) -> None:
        p = _make_3mf(tmp_path, {"3D/model.model": b"<xml/>"})
        assert extract_embedded_3mf_thumbnail(p) is None

    def test_returns_none_for_corrupt_archive(self, tmp_path: Path) -> None:
        p = tmp_path / "broken.3mf"
        p.write_bytes(b"this is not a zip file")
        assert extract_embedded_3mf_thumbnail(p) is None


class TestPngMagic:
    def test_rejects_data_without_png_magic(self, tmp_path: Path) -> None:
        p = _make_3mf(tmp_path, {"Metadata/thumbnail.png": b"not actually a png"})
        assert extract_embedded_3mf_thumbnail(p) is None
