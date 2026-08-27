"""Destructive storage roles are accepted only across proven path boundaries.

These tests protect operator data from alias, nesting, and symlink mistakes
before storage workers are allowed to mutate anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.services.storage_paths import (
    StoragePathOverlapError,
    canonical_path,
    paths_overlap,
    sqlite_database_path,
    unlink_managed_file,
    validate_disjoint_directories,
    validate_file_outside_roots,
    validate_path_outside_roots,
    validate_runtime_storage_paths,
)


class TestCanonicalPath:
    def test_resolves_a_symlinked_parent_when_the_leaf_is_missing(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)

        result = canonical_path(alias / "future.bin")

        assert result == real / "future.bin"


class TestSqliteDatabasePath:
    def test_decodes_and_canonicalizes_a_file_backed_sqlite_url(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "vault db.sqlite"
        encoded = str(database).replace(" ", "%20")

        result = sqlite_database_path(f"sqlite:///{encoded}")

        assert result == database

    @pytest.mark.parametrize(
        "db_url",
        [
            pytest.param("sqlite:///:memory:", id="sqlite-memory"),
            pytest.param("postgresql://db/vault", id="postgresql"),
        ],
    )
    def test_returns_none_for_a_database_without_a_sqlite_file(
        self, db_url: str
    ) -> None:
        assert sqlite_database_path(db_url) is None


class TestPathsOverlap:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param(Path("/vault"), Path("/vault"), id="equal"),
            pytest.param(Path("/vault"), Path("/vault/files"), id="parent-first"),
            pytest.param(Path("/vault/files"), Path("/vault"), id="child-first"),
        ],
    )
    def test_detects_equal_or_nested_paths(self, first: Path, second: Path) -> None:
        assert paths_overlap(first, second) is True

    def test_accepts_sibling_paths(self) -> None:
        assert paths_overlap(Path("/vault-a"), Path("/vault-b")) is False


class TestValidateDisjointDirectories:
    def test_returns_canonical_disjoint_roots(self, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"

        result = validate_disjoint_directories({"first": first, "second": second})

        assert result == {"first": first.resolve(), "second": second.resolve()}

    def test_rejects_a_symlink_alias(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)

        with pytest.raises(StoragePathOverlapError) as exc_info:
            validate_disjoint_directories({"data": real, "staging": alias})

        assert (exc_info.value.first, exc_info.value.second) == ("data", "staging")


class TestValidatePathOutsideRoots:
    def test_returns_a_candidate_outside_every_protected_root(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "candidate"

        result = validate_path_outside_roots(
            candidate, {"data": tmp_path / "data", "backup": tmp_path / "backup"}
        )

        assert result == candidate.resolve()

    def test_rejects_a_candidate_nested_beneath_a_protected_root(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "data"

        with pytest.raises(StoragePathOverlapError) as exc_info:
            validate_path_outside_roots(root / "nested", {"data": root})

        assert (exc_info.value.first, exc_info.value.second) == ("candidate", "data")


class TestUnlinkManagedFile:
    def test_unlinks_a_regular_leaf_beneath_the_managed_root(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "staging"
        root.mkdir()
        candidate = root / "operation.bin"
        candidate.write_bytes(b"owned")

        removed = unlink_managed_file(candidate, root)

        assert removed is True
        assert candidate.exists() is False

    def test_reports_a_missing_managed_leaf_without_error(self, tmp_path: Path) -> None:
        root = tmp_path / "staging"
        root.mkdir()

        removed = unlink_managed_file(root / "missing.bin", root)

        assert removed is False

    def test_rejects_a_leaf_symlink_without_touching_its_target(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "other-user-file.bin"
        target.write_bytes(b"preserve")
        link = root / "operation-temp.bin"
        link.symlink_to(target)

        with pytest.raises(StoragePathOverlapError, match="symlink"):
            unlink_managed_file(link, root)

        assert link.is_symlink()
        assert target.read_bytes() == b"preserve"

    def test_rejects_an_intermediate_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "staging"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        target = outside / "keep.bin"
        target.write_bytes(b"preserve")
        (root / "redirect").symlink_to(outside, target_is_directory=True)

        with pytest.raises(StoragePathOverlapError, match="outside_root"):
            unlink_managed_file(root / "redirect" / "keep.bin", root)

        assert target.read_bytes() == b"preserve"


class TestValidateRuntimeStoragePaths:
    def test_returns_safe_runtime_roots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(_overlay, "data_dir", tmp_path / "data")
        monkeypatch.setitem(_overlay, "thumb_dir", tmp_path / "thumbs")
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path / "staging")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path / "backups")
        monkeypatch.setitem(
            _overlay, "db_url", f"sqlite:///{tmp_path / 'vault.sqlite'}"
        )
        monkeypatch.setitem(_overlay, "secrets_key_file", tmp_path / "secrets.key")

        result = validate_runtime_storage_paths()

        assert result == {
            "data_dir": (tmp_path / "data").resolve(),
            "thumb_dir": (tmp_path / "thumbs").resolve(),
            "staging_dir": (tmp_path / "staging").resolve(),
            "backup_dir": (tmp_path / "backups").resolve(),
        }

    def test_rejects_overlapping_runtime_roots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = tmp_path / "managed"
        monkeypatch.setitem(_overlay, "data_dir", root)
        monkeypatch.setitem(_overlay, "thumb_dir", root / "thumbs")
        monkeypatch.setitem(_overlay, "staging_dir", tmp_path / "staging")
        monkeypatch.setitem(_overlay, "backup_dir", tmp_path / "backups")

        with pytest.raises(StoragePathOverlapError) as exc_info:
            validate_runtime_storage_paths()

        assert (exc_info.value.first, exc_info.value.second) == (
            "data_dir",
            "thumb_dir",
        )


class TestValidateFileOutsideRoots:
    def test_returns_a_file_outside_every_mutable_root(self, tmp_path: Path) -> None:
        candidate = tmp_path / "vault.sqlite"

        result = validate_file_outside_roots(candidate, {"data": tmp_path / "data"})

        assert result == candidate.resolve()

    def test_rejects_a_file_inside_a_mutable_root(self, tmp_path: Path) -> None:
        root = tmp_path / "data"

        with pytest.raises(StoragePathOverlapError) as exc_info:
            validate_file_outside_roots(root / "vault.sqlite", {"data": root})

        assert (exc_info.value.first, exc_info.value.second) == (
            "managed_file",
            "data",
        )
