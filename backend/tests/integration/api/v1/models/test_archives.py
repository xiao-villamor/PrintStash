"""Defends archives at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    _zip_bytes,
    io,
    pytest,
    zipfile,
)


def test_inspect_archive_rejects_traversal_instead_of_partially_accepting(tmp_path):
    from app.services import importer

    archive = tmp_path / "pack.zip"
    archive.write_bytes(
        _zip_bytes(
            {
                "good.stl": b"solid",
                "nested/part.3mf": b"x",
                "../evil.stl": b"x",  # traversal — must be dropped
                "readme.txt": b"hi",  # not importable, not image
                "preview.png": b"img",  # image (kept, marked)
            }
        )
    )
    with pytest.raises(importer.ImportError_, match="archive_unsafe_entry"):
        importer.inspect_archive(archive)


def test_inspect_archive_counts_directory_records_against_cap(tmp_path):
    """Every central-directory record consumes parser resources and is capped."""
    from app.core.config import _overlay
    from app.services import importer

    _overlay["max_archive_entries"] = 3
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # 4 directory records, only 2 real files — over the cap by
            # raw entry count, under it by file count.
            for d in ["a/", "a/b/", "a/b/c/", "a/b/c/d/"]:
                zf.writestr(d, b"")
            zf.writestr("a/b/c/d/part.stl", b"solid")
            zf.writestr("a/b/preview.png", b"img")
        archive = tmp_path / "nested.zip"
        archive.write_bytes(buf.getvalue())

        with pytest.raises(importer.ImportError_, match="archive_too_many_entries"):
            importer.inspect_archive(archive)
    finally:
        _overlay.pop("max_archive_entries", None)


def test_inspect_archive_enforces_depth_32_and_rejects_depth_33(tmp_path):
    from app.services import importer

    accepted = tmp_path / "depth-32.zip"
    accepted.write_bytes(_zip_bytes({"/".join(["d"] * 32 + ["part.stl"]): b"x"}))
    assert len(importer.inspect_archive(accepted)) == 1

    rejected = tmp_path / "depth-33.zip"
    rejected.write_bytes(_zip_bytes({"/".join(["d"] * 33 + ["part.stl"]): b"x"}))
    with pytest.raises(importer.ImportError_, match="archive_path_too_deep"):
        importer.inspect_archive(rejected)


def test_inspect_archive_rejects_unicode_normalized_duplicates(tmp_path):
    from app.services import importer

    archive = tmp_path / "unicode-duplicates.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Caf\N{LATIN SMALL LETTER E WITH ACUTE}.stl", b"one")
        zf.writestr("Cafe\N{COMBINING ACUTE ACCENT}.STL", b"two")

    with pytest.raises(importer.ImportError_, match="archive_duplicate_entry"):
        importer.inspect_archive(archive)


def test_archive_entries_have_stable_selection_ids(tmp_path):
    from app.services import importer

    archive = tmp_path / "ids.zip"
    archive.write_bytes(_zip_bytes({"a.stl": b"a", "b.stl": b"bb"}))

    entries = importer.inspect_archive(archive)

    assert len({entry.entry_id for entry in entries}) == 2
    assert all(entry.entry_id.count(":") == 2 for entry in entries)


def test_extract_selected_only_returns_importable(tmp_path):
    from app.core.config import _overlay
    from app.services import importer

    _overlay["staging_dir"] = tmp_path  # write staged files into the tmp dir
    archive = tmp_path / "pack.zip"
    archive.write_bytes(_zip_bytes({"a.stl": b"solid", "notes.txt": b"x"}))
    out = importer.extract_selected(archive, ["a.stl", "notes.txt"])
    assert len(out) == 1
    staged, name = out[0]
    assert name == "a.stl" and staged.exists()
    staged.unlink(missing_ok=True)
