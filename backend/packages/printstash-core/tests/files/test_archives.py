"""Reading a user-uploaded ZIP without letting it decide where bytes land.

A model archive comes from Printables, MakerWorld, or a browser upload — never
from PrintStash — and every field inside it is chosen by whoever built it: entry
names, declared sizes, entry count, nesting depth. Extracting one naively is the
classic zip-slip: an entry named `../../../etc/cron.d/x` writes wherever the
process can reach.

So this module treats an archive as a manifest to be validated, and only then as
bytes to be copied. The refusals are the substance of the file:

**Names are refused, not sanitised.** Traversal, an absolute path, a Windows
drive prefix, a backslash separator — each is rejected rather than rewritten,
because a rewrite means guessing what the archive meant and every guess is a
place to be wrong. Unicode-normalised duplicates are refused too: `café.stl` in
NFC and NFD are one file on macOS and two on Linux, and accepting both would let
one entry overwrite the other during extraction.

**Every limit is enforced from the central directory first.** Entry count, per-
entry size, total uncompressed size, path length, and nesting depth are all
checked before anything is read, so a zip bomb is refused at inspection time
rather than discovered when the disk fills. The *declared* size is checked and
then the *actual* bytes are counted while streaming, because a ZIP header can lie.

**Only importable entries are staged.** A selection naming a `.txt` gets it
skipped rather than extracted, so a user cannot smuggle an arbitrary file into
the library by asking for it by name.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import printstash_core.files.archives as archives_module
from printstash_core.files import (
    ArchiveLimits,
    ArchivePolicyError,
    extract_selected,
    inspect_archive,
    safe_entry_name,
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


def _inspect(path: Path, **overrides: int) -> list:
    return inspect_archive(
        path,
        limits=_limits(**overrides),
        file_types=FILE_TYPES,
        image_suffixes=IMAGES,
    )


class TestInspectArchive:
    def test_preserves_unrelated_native_errors(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from printstash_core.files import archives

        def reject(*_args, **_kwargs):
            raise ValueError("native_contract_error")

        monkeypatch.setattr(
            archives,
            "kernel",
            lambda: SimpleNamespace(inspect_archive=reject),
        )

        with pytest.raises(ValueError, match="native_contract_error"):
            _inspect(tmp_path / "unused.zip")

    def test_lists_a_supported_model_file_with_its_type(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "bundle.zip", {"parts/a.stl": b"a"})

        entries = _inspect(path)

        assert [(entry.name, entry.file_type, entry.is_image) for entry in entries] == [
            ("parts/a.stl", "stl", False)
        ]

    def test_lists_an_image_without_a_file_type(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "bundle.zip", {"preview.png": b"p"})

        entries = _inspect(path)

        # Images become thumbnails rather than library files, so they are listed
        # but carry no importable type.
        assert [(entry.name, entry.is_image) for entry in entries] == [
            ("preview.png", True)
        ]

    def test_omits_an_entry_that_is_neither(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "bundle.zip", {"a.stl": b"a", "notes.txt": b"n"})

        # A README is not something PrintStash can do anything with, and offering
        # it in the picker would invite a user to import it.
        assert [entry.name for entry in _inspect(path)] == ["a.stl"]

    def test_gives_each_entry_a_stable_identifier(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "bundle.zip", {"parts/a.stl": b"a"})

        entries = _inspect(path)

        # The id is what a selection refers to across the two requests that
        # inspect and then import, so it has to be derivable from the archive
        # rather than from list position.
        assert all(entry.entry_id.count(":") == 2 for entry in entries)

    def test_returns_nothing_for_an_archive_with_no_usable_entries(
        self, tmp_path: Path
    ) -> None:
        path = _archive(tmp_path / "bundle.zip", {"notes.txt": b"n"})

        assert _inspect(path) == []

    @pytest.mark.parametrize(
        "name", ["../escape.stl", "/absolute.stl", "C:\\escape.stl"]
    )
    def test_refuses_an_entry_that_could_escape_the_extraction_root(
        self, tmp_path: Path, name: str
    ) -> None:
        path = _archive(tmp_path / "unsafe.zip", {name: b"x"})

        # Refused rather than rewritten: a rewrite means guessing what the
        # archive meant, and every guess is a place to be wrong.
        with pytest.raises(ArchivePolicyError, match="archive_unsafe_entry"):
            _inspect(path)

    def test_refuses_two_entries_that_normalize_to_one_name(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "duplicates.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Caf\N{LATIN SMALL LETTER E WITH ACUTE}.stl", b"one")
            archive.writestr("Cafe\N{COMBINING ACUTE ACCENT}.STL", b"two")

        # NFC and NFD spellings are one file on macOS and two on Linux, and the
        # case differs as well. Accepting both would let one entry overwrite the
        # other during extraction, silently.
        with pytest.raises(ArchivePolicyError, match="archive_duplicate_entry"):
            _inspect(path)

    def test_refuses_two_entries_that_full_casefold_to_one_name(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "casefold-duplicates.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Maße.stl", b"one")
            archive.writestr("MASSE.stl", b"two")

        with pytest.raises(ArchivePolicyError, match="archive_duplicate_entry"):
            _inspect(path)

    def test_refuses_a_symbolic_link_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "symlink.zip"
        link = zipfile.ZipInfo("part.stl")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(link, b"../target")

        with pytest.raises(ArchivePolicyError, match="archive_unsafe_entry"):
            _inspect(path)

    def test_refuses_more_entries_than_the_limit(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "large.zip", {"a.stl": b"12", "b.stl": b"34"})

        with pytest.raises(ArchivePolicyError, match="archive_too_many_entries"):
            _inspect(path, max_entries=1)

    def test_refuses_more_total_bytes_than_the_limit(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "large.zip", {"a.stl": b"12", "b.stl": b"34"})

        # Checked from the central directory before anything is read, so a zip
        # bomb costs nothing to refuse.
        with pytest.raises(ArchivePolicyError, match="archive_too_large"):
            _inspect(path, max_total_bytes=3)

    def test_refuses_a_file_that_is_not_a_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "invalid.zip"
        path.write_bytes(b"not a zip")

        # A truncated download or a mislabelled file must produce a named
        # provider error, not a `BadZipFile` traceback in the import job.
        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _inspect(path)

    def test_preserves_an_unexpected_native_value_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class BrokenKernel:
            @staticmethod
            def inspect_archive(*_args, **_kwargs):
                raise ValueError("native contract bug")

        monkeypatch.setattr(archives_module, "kernel", lambda: BrokenKernel())

        with pytest.raises(ValueError, match="native contract bug") as raised:
            _inspect(tmp_path / "unused.zip")

        assert not isinstance(raised.value, ArchivePolicyError)


class TestExtractSelected:
    def test_stages_the_bytes_of_a_selected_entry(self, tmp_path: Path) -> None:
        path = _archive(tmp_path / "bundle.zip", {"a.stl": b"solid"})
        staging = tmp_path / "staging"

        extracted = extract_selected(
            path,
            ["a.stl"],
            staging_dir=staging,
            max_entry_bytes=1024,
            importable_suffixes=set(FILE_TYPES),
            name_factory=lambda suffix: f"fixed{suffix}",
        )

        assert extracted == [(staging / "fixed.stl", "a.stl")]
        assert extracted[0][0].read_bytes() == b"solid"

    def test_skips_a_selected_entry_that_is_not_importable(
        self, tmp_path: Path
    ) -> None:
        path = _archive(tmp_path / "bundle.zip", {"a.stl": b"solid", "notes.txt": b"n"})

        extracted = extract_selected(
            path,
            ["a.stl", "notes.txt"],
            staging_dir=tmp_path / "staging",
            max_entry_bytes=1024,
            importable_suffixes=set(FILE_TYPES),
            name_factory=lambda suffix: f"fixed{suffix}",
        )

        # The selection comes from the client. Extracting whatever it names
        # would let a user smuggle an arbitrary file into the library by asking
        # for it explicitly.
        assert [source for _staged, source in extracted] == ["a.stl"]

    def test_names_the_staged_file_from_the_factory_it_was_given(
        self, tmp_path: Path
    ) -> None:
        path = _archive(tmp_path / "bundle.zip", {"parts/a.stl": b"solid"})

        extracted = extract_selected(
            path,
            ["parts/a.stl"],
            staging_dir=tmp_path / "staging",
            max_entry_bytes=1024,
            importable_suffixes=set(FILE_TYPES),
            name_factory=lambda suffix: f"generated{suffix}",
        )

        # The archive's own name never reaches the filesystem: the caller
        # supplies an opaque staged name and keeps the original as metadata.
        assert extracted[0][0].name == "generated.stl"


class TestSafeEntryName:
    """The path check that stands between an upload and the rest of the disk."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("dir/", id="trailing-slash"),
            pytest.param("dir\\", id="trailing-backslash"),
            pytest.param("/etc/passwd", id="absolute-posix"),
            pytest.param("\\windows\\system32", id="absolute-windows"),
            pytest.param("C:/secrets.txt", id="drive-letter"),
            pytest.param("../escape.stl", id="traversal"),
            pytest.param("a/../../escape.stl", id="traversal-mid-path"),
            pytest.param("..\\escape.stl", id="traversal-backslash"),
        ],
    )
    def test_refuses_a_name_that_could_escape_the_staging_directory(
        self, name: str
    ) -> None:
        # Every one of these, joined to a staging path, resolves somewhere the
        # archive has no business writing.
        assert safe_entry_name(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("part.stl", id="root"),
            pytest.param("nested/part.stl", id="nested"),
            pytest.param("a/b/c/part.stl", id="deep"),
            pytest.param("nested\\part.stl", id="windows-separator"),
            pytest.param("with..dots.stl", id="dots-in-a-filename"),
        ],
    )
    def test_accepts_an_ordinary_relative_entry(self, name: str) -> None:
        # `with..dots.stl` is the interesting one: a substring `..` is not a
        # traversal, and rejecting it would break real slicer output.
        assert safe_entry_name(name) is True


class TestSafeSubdir:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            pytest.param("part.stl", "", id="root-is-empty"),
            pytest.param("nested/part.stl", "nested", id="one-level"),
            pytest.param("a/b/part.stl", "a/b", id="two-levels"),
            pytest.param("nested\\part.stl", "nested", id="windows-separator"),
        ],
    )
    def test_returns_the_posix_directory_part(self, name: str, expected: str) -> None:
        # The root case returns `""` rather than `"."`, because the caller joins
        # this onto a collection path and `"."` would become a literal directory.
        assert safe_subdir(name) == expected


class TestInspectArchiveLimits:
    def test_refuses_an_oversized_central_directory(self, tmp_path: Path) -> None:
        archive = _archive(
            tmp_path / "many.zip", {f"f{i}.stl": b"x" for i in range(40)}
        )

        # The central directory is read before any entry, so a bomb hidden there
        # has to be refused on its declared size alone.
        with pytest.raises(ArchivePolicyError, match="archive_too_large"):
            inspect_archive(
                archive,
                limits=_limits(max_central_directory_bytes=10),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_refuses_a_path_longer_than_the_byte_budget(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "long.zip", {("a" * 300) + ".stl": b"x"})

        # Bytes, not characters: a multi-byte name can be far longer on disk than
        # it looks, which is how a path limit gets bypassed.
        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            inspect_archive(
                archive,
                limits=_limits(max_path_bytes=64),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_refuses_a_path_nested_deeper_than_the_limit(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "deep.zip", {"a/b/c/d/e/part.stl": b"x"})

        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            inspect_archive(
                archive,
                limits=_limits(max_depth=2),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )

    def test_skips_directory_entries_without_counting_them(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "dirs.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/", b"")
            archive.writestr("nested/part.stl", b"x")

        entries = inspect_archive(
            path,
            limits=_limits(),
            file_types=FILE_TYPES,
            image_suffixes=IMAGES,
        )

        # A directory entry carries no bytes and must not consume the entry
        # budget, or a deeply-foldered archive fails for the wrong reason.
        assert [entry.name for entry in entries] == ["nested/part.stl"]

    def test_refuses_a_single_entry_over_the_entry_budget(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "big.zip", {"part.stl": b"x" * 200})

        with pytest.raises(ArchivePolicyError, match="archive_entry_too_large"):
            inspect_archive(
                archive,
                limits=_limits(max_entry_bytes=100),
                file_types=FILE_TYPES,
                image_suffixes=IMAGES,
            )


class TestExtractSelectedFailures:
    def test_removes_prior_outputs_when_native_extraction_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from printstash_core.files import archives

        class FailingArchive:
            def __init__(self, _path: Path) -> None:
                pass

            def selected_entries(self, *_args):
                return [(0, ".stl", "first.stl"), (1, ".stl", "second.stl")]

            def extract_to(
                self, index: int, destination: Path, _max_entry_bytes: int
            ) -> None:
                if index == 1:
                    raise ValueError("archive_extract_failed")
                destination.write_bytes(b"first")

            def close(self) -> None:
                pass

        monkeypatch.setattr(
            archives,
            "kernel",
            lambda: SimpleNamespace(NativeArchive=FailingArchive),
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        with pytest.raises(ArchivePolicyError, match="archive_extract_failed"):
            extract_selected(
                tmp_path / "unused.zip",
                ["first.stl", "second.stl"],
                staging_dir=staging,
                max_entry_bytes=100,
                importable_suffixes={".stl"},
            )

        assert list(staging.iterdir()) == []

    def test_removes_everything_it_staged_when_one_entry_is_refused(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(
            tmp_path / "mixed.zip",
            {"good.stl": b"x", "huge.stl": b"x" * 500},
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        with pytest.raises(ArchivePolicyError, match="archive_entry_too_large"):
            extract_selected(
                archive,
                ["good.stl", "huge.stl"],
                staging_dir=staging,
                max_entry_bytes=100,
                importable_suffixes={".stl"},
            )

        # All-or-nothing: a half-extracted archive leaves staged bytes that no
        # row owns, and nothing will ever clean them up.
        assert list(staging.iterdir()) == []

    def test_removes_staged_files_when_a_later_destination_fails(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(
            tmp_path / "two.zip", {"first.stl": b"one", "second.stl": b"two"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()
        calls = 0

        def destination(suffix: str) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("destination unavailable")
            return f"first{suffix}"

        with pytest.raises(RuntimeError, match="destination unavailable"):
            extract_selected(
                archive,
                ["first.stl", "second.stl"],
                staging_dir=staging,
                max_entry_bytes=100,
                importable_suffixes={".stl"},
                name_factory=destination,
            )

        assert calls == 2
        assert list(staging.iterdir()) == []

    def test_refuses_an_unsafe_entry_that_was_explicitly_selected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "evil.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../escape.stl", b"x")
        staging = tmp_path / "staging"
        staging.mkdir()

        # `inspect_archive` would have refused this archive already; the check is
        # repeated here because the name list arrives from the client, and a
        # caller could name an entry the inspection never returned.
        with pytest.raises(ArchivePolicyError, match="archive_unsafe_entry"):
            extract_selected(
                path,
                ["../escape.stl"],
                staging_dir=staging,
                max_entry_bytes=1024,
                importable_suffixes={".stl"},
            )

        assert list(staging.iterdir()) == []

    def test_skips_a_selected_entry_whose_type_is_not_importable(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(
            tmp_path / "readme.zip", {"part.stl": b"x", "notes.txt": b"hello"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        extracted = extract_selected(
            archive,
            ["part.stl", "notes.txt"],
            staging_dir=staging,
            max_entry_bytes=1024,
            importable_suffixes={".stl"},
        )

        # Selecting a README is not an error — it is simply not imported.
        assert [name for _staged, name in extracted] == ["part.stl"]

    def test_names_each_staged_file_uniquely_by_default(self, tmp_path: Path) -> None:
        archive = _archive(
            tmp_path / "two.zip", {"a/part.stl": b"x", "b/part.stl": b"y"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()

        extracted = extract_selected(
            archive,
            ["a/part.stl", "b/part.stl"],
            staging_dir=staging,
            max_entry_bytes=1024,
            importable_suffixes={".stl"},
        )

        # Two entries can share a basename in different folders; staging them
        # under that name would have the second overwrite the first.
        staged = [path.name for path, _name in extracted]
        assert len(set(staged)) == 2
        assert all(name.endswith(".stl") for name in staged)

    def test_preserves_an_existing_staging_destination(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path / "part.zip", {"part.stl": b"replacement"})
        staging = tmp_path / "staging"
        staging.mkdir()
        existing = staging / "fixed.stl"
        existing.write_bytes(b"existing")

        with pytest.raises(OSError):
            extract_selected(
                archive,
                ["part.stl"],
                staging_dir=staging,
                max_entry_bytes=1024,
                importable_suffixes={".stl"},
                name_factory=lambda suffix: f"fixed{suffix}",
            )

        assert existing.read_bytes() == b"existing"


class TestIncrementalExtraction:
    def test_expands_only_one_entry_at_a_time(self, tmp_path):
        from printstash_core.files import iter_selected

        archive = tmp_path / "parts.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("one.stl", b"first")
            output.writestr("two.stl", b"second")
        staging = tmp_path / "staging"
        staging.mkdir()
        selected = iter_selected(
            archive,
            ["one.stl", "two.stl"],
            staging_dir=staging,
            max_entry_bytes=100,
            importable_suffixes={".stl"},
        )
        first, _ = next(selected)
        assert first.read_bytes() == b"first"
        assert len(list(staging.iterdir())) == 1
        second, _ = next(selected)
        assert not first.exists()
        assert second.read_bytes() == b"second"
        selected.close()
        assert not list(staging.iterdir())


class TestArchivesContract:
    @pytest.mark.parametrize(
        "entries,names",
        [
            ({"folder/": b""}, ["folder/"]),
            ({"notes.txt": b"notes"}, ["notes.txt"]),
            ({"part.stl": b"model"}, []),
        ],
    )
    def test_incremental_extraction_skips_non_model_selections(
        self, tmp_path, entries, names
    ):
        from printstash_core.files import iter_selected

        archive = _archive(tmp_path / "selection.zip", entries)
        staging = tmp_path / "staging"
        staging.mkdir()
        assert (
            list(
                iter_selected(
                    archive,
                    names,
                    staging_dir=staging,
                    max_entry_bytes=100,
                    importable_suffixes={".stl"},
                )
            )
            == []
        )
        assert list(staging.iterdir()) == []

    @pytest.mark.parametrize(
        "name,payload,reason",
        [
            ("../escape.stl", b"model", "archive_unsafe_entry"),
            ("large.stl", b"x" * 101, "archive_entry_too_large"),
        ],
    )
    def test_incremental_extraction_revalidates_selected_entries(
        self, tmp_path, name, payload, reason
    ):
        from printstash_core.files import iter_selected

        archive = _archive(tmp_path / "unsafe.zip", {name: payload})
        staging = tmp_path / "staging"
        staging.mkdir()
        with pytest.raises(ArchivePolicyError, match=reason):
            list(
                iter_selected(
                    archive,
                    [name],
                    staging_dir=staging,
                    max_entry_bytes=100,
                    importable_suffixes={".stl"},
                )
            )
        assert list(staging.iterdir()) == []

    def test_incremental_extraction_cleans_up_after_exhaustion(self, tmp_path):
        from printstash_core.files import iter_selected

        archive = _archive(tmp_path / "selected.zip", {"part.stl": b"model"})
        staging = tmp_path / "staging"
        staging.mkdir()
        observed = [
            (name, path.read_bytes())
            for path, name in iter_selected(
                archive,
                ["part.stl", "part.stl"],
                staging_dir=staging,
                max_entry_bytes=100,
                importable_suffixes={".stl"},
            )
        ]
        assert observed == [("part.stl", b"model")]
        assert list(staging.iterdir()) == []

    def test_eager_extraction_skips_unselected_entries(self, tmp_path):
        archive = _archive(
            tmp_path / "selection.zip", {"other.stl": b"other", "part.stl": b"model"}
        )
        staging = tmp_path / "staging"
        staging.mkdir()
        extracted = extract_selected(
            archive,
            ["part.stl"],
            staging_dir=staging,
            max_entry_bytes=100,
            importable_suffixes={".stl"},
        )
        assert [(name, path.read_bytes()) for path, name in extracted] == [
            ("part.stl", b"model")
        ]
