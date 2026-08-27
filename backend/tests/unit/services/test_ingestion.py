"""Ingestion chooses a collision-safe destination without changing suffixes."""

from __future__ import annotations

from pathlib import Path

from app.services.ingestion import _collision_safe_path


class TestCollisionSafePath:
    def test_returns_name_when_free(self, tmp_path: Path) -> None:
        assert _collision_safe_path(tmp_path, "model.stl").name == "model.stl"

    def test_appends_next_free_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "model.stl").write_text("x")
        assert _collision_safe_path(tmp_path, "model.stl").name == "model-2.stl"
        (tmp_path / "model-2.stl").write_text("x")
        assert _collision_safe_path(tmp_path, "model.stl").name == "model-3.stl"

    def test_keeps_suffix_when_disambiguating(self, tmp_path: Path) -> None:
        (tmp_path / "part.3mf").write_text("x")
        out = _collision_safe_path(tmp_path, "part.3mf")
        assert out.suffix == ".3mf" and out.stem == "part-2"

    def test_handles_extensionless_name(self, tmp_path: Path) -> None:
        (tmp_path / "README").write_text("x")
        assert _collision_safe_path(tmp_path, "README").name == "README-2"
