"""Archive names remain traversal-safe and map to stable collection paths."""

from __future__ import annotations

import pytest

from app.services import importer as imp


class TestSafeEntryName:
    @pytest.mark.parametrize("name", ["model.stl", "sub/dir/model.stl", "a/b/c.3mf"])
    def test_accepts_relative_paths(self, name: str) -> None:
        assert imp._safe_entry_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "dir/",  # directory entry
            "/etc/passwd",  # absolute posix
            "\\windows\\x",  # absolute windows
            "../evil.stl",  # posix traversal
            "a/../../evil.stl",  # traversal mid-path
            "..\\..\\evil.stl",  # regression: backslash traversal on posix
            "C:\\evil.stl",  # drive letter
        ],
    )
    def test_rejects_unsafe(self, name: str) -> None:
        assert imp._safe_entry_name(name) is False


class TestCollectionForArchive:
    def test_nests_under_parent(self) -> None:
        assert (
            imp._collection_for_archive("Functional", "Brackets.zip")
            == "Functional/Brackets"
        )

    def test_no_parent_uses_archive_stem(self) -> None:
        assert imp._collection_for_archive(None, "MyPack.zip") == "MyPack"

    def test_blank_parent_is_ignored(self) -> None:
        assert imp._collection_for_archive("   ", "Pack.zip") == "Pack"

    def test_trailing_slash_on_parent_collapses(self) -> None:
        assert imp._collection_for_archive("A/B/", "Pack.zip") == "A/B/Pack"
