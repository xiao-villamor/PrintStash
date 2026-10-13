"""Unavailable embedded previews return no image. Missing candidates and corrupt archives leave the caller able to select a rendered preview."""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.modules.media import (
    mesh_previews,
)


class TestExtractEmbedded3mfThumbnail:
    def test_extract_embedded_3mf_thumbnail_no_candidates_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        p = tmp_path / "no-preview.3mf"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", b"<mesh/>")
        assert mesh_previews.extract_embedded_3mf_thumbnail(p) is None

    def test_extract_embedded_3mf_thumbnail_survives_corrupt_archive(
        self,
        tmp_path: Path,
    ) -> None:
        p = tmp_path / "corrupt.3mf"
        p.write_bytes(b"not a zip archive")
        assert mesh_previews.extract_embedded_3mf_thumbnail(p) is None
