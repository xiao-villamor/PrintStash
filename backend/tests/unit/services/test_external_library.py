"""External-library discovery and scheduling preserve mirror semantics."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
)
from app.services.external_library import _collection_path_for, _walk, is_due


class TestWalk:
    def test_includes_supported_suffixes_case_insensitively(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.stl").write_text("x")
        (tmp_path / "b.STL").write_text("x")  # uppercase ext still counts
        (tmp_path / "c.txt").write_text("x")  # unsupported, ignored
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "d.3mf").write_text("x")  # recurses

        names = {Path(k).name for k in _walk(tmp_path)}
        assert names == {"a.stl", "b.STL", "d.3mf"}

    def test_empty_dir_yields_nothing(self, tmp_path: Path) -> None:
        assert _walk(tmp_path) == {}


class TestCollectionPathForMirror:
    def _lib(self, root: str) -> ExternalLibrary:
        return ExternalLibrary(
            name="L",
            root_path=root,
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

    def test_nested_dirs_become_collection_path(self) -> None:
        lib = self._lib("/nas/lib")
        out = _collection_path_for(
            None, lib, Path("/nas/lib/Functional/Brackets/x.stl")
        )
        assert out == "Functional/Brackets"

    def test_root_level_file_has_no_collection(self) -> None:
        lib = self._lib("/nas/lib")
        assert _collection_path_for(None, lib, Path("/nas/lib/x.stl")) is None

    def test_file_outside_root_returns_none(self) -> None:
        lib = self._lib("/nas/lib")
        assert _collection_path_for(None, lib, Path("/elsewhere/x.stl")) is None

    def test_trailing_slash_on_root_is_tolerated(self) -> None:
        lib = self._lib("/nas/lib/")
        out = _collection_path_for(None, lib, Path("/nas/lib/Toys/x.stl"))
        assert out == "Toys"


class TestIsDue:
    NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_invalid_schedule_never_due(self) -> None:
        assert is_due("not a cron", None, self.NOW) is False

    def test_empty_schedule_never_due(self) -> None:
        assert is_due("", None, self.NOW) is False

    def test_never_scanned_with_valid_schedule_is_due(self) -> None:
        assert is_due("0 * * * *", None, self.NOW) is True

    def test_due_when_next_fire_has_passed(self) -> None:
        last = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        assert is_due("*/30 * * * *", last, self.NOW) is True

    def test_not_due_before_next_fire(self) -> None:
        last = datetime(2024, 1, 1, 11, 59, 0, tzinfo=timezone.utc)
        assert is_due("0 0 * * *", last, self.NOW) is False
