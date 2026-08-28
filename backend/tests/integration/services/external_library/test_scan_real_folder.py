"""The scanner against a real folder of real models, not synthesised fixtures.

Every other test in this folder writes bytes it controls, which proves the
scanning logic but cannot prove the thing a user actually hits first: that the
files people really have — slicer output from several vendors, meshes exported by
several tools, mixed casing, spaces and unicode in filenames — are all recognised
and parsed. A fixture cube is a mesh the way a mock is a printer.

So this scans the repo's `testdata/` folder, or whatever `PRINTSTASH_TEST_NAS_DIR`
points at, and asserts that every file the walker's own suffix filter admits ends
up indexed. It skips when there is nothing to point it at, which is why it is in
its own file: it is the one test here whose absence is normal."""

from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session

from app.db.models import (
    Model,
)
from app.services import external_library
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    enable_feature,
    external_files,
)
from tests.paths import TESTDATA_DIR, require_fixtures

# Defaults to the repo's ``testdata/`` folder; override with PRINTSTASH_TEST_NAS_DIR.
_REPO_TESTDATA = TESTDATA_DIR


def _real_nas_dir() -> Path:
    """The folder to scan: `testdata/` by default, overridable for a bigger corpus.

    Not optional. `testdata/` is committed, so it is always there, and the previous
    `skipif` meant this test — the only one that runs the scanner over real slicer
    output rather than synthetic files — vanished from any run where the check went
    wrong, silently.
    """
    env = os.environ.get("PRINTSTASH_TEST_NAS_DIR")
    if env:
        return Path(env)
    require_fixtures(_REPO_TESTDATA)
    return _REPO_TESTDATA


def _supported_files(root: Path) -> list[Path]:
    """Files under *root* the scanner recognises (mirrors ``_walk``'s filter)."""
    from app.db.models import SUFFIX_TO_FILE_TYPE

    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUFFIX_TO_FILE_TYPE
    ]


class TestScanLibrary:
    def test_scan_real_world_folder(self, tmp_path: Path, db_session: Session) -> None:
        """Scan the engine against real STL/3MF/OBJ/g-code files (repo ``testdata/``).

        Every supported file (including PrusaSlicer binary ``.bgcode``) must index in
        place without a parse error, point at a real non-empty on-disk path, and an
        immediate rescan must be a clean no-op. Unsupported files are silently
        ignored, never errored.
        """
        use_local_storage(tmp_path)
        enable_feature(db_session)
        root = _real_nas_dir()
        assert root is not None
        expected = _supported_files(root)
        assert expected, f"no supported model/g-code files found under {root}"

        lib = build_external_library(db_session, root, name="nas")
        summary = external_library.scan_library(lib.id)

        assert summary["aborted"] is False
        # Every supported file indexed, and no real file tripped a parse/ingest error.
        assert summary["errors"] == [], summary["errors"]
        assert summary["added"] == len(expected)

        files = external_files(db_session)
        assert len(files) == len(expected)
        indexed_paths = {Path(f.path) for f in files}
        assert indexed_paths == set(expected)
        for f in files:
            assert Path(f.path).exists()
            assert str(f.path).startswith(str(root))
            assert f.size_bytes > 0
            assert f.is_external is True

        # Folder hierarchy mirrors into collections: a file's subfolder chain becomes
        # its collection path; files sitting at the root get no collection.
        for f in files:
            rel_parent = Path(f.path).parent.relative_to(root)
            model = db_session.get(Model, f.model_id)
            if rel_parent == Path("."):
                assert model.collection_rel is None
            else:
                assert model.collection_rel is not None
                assert model.collection_rel.path == rel_parent.as_posix()

        # Idempotent: a second scan of an unchanged real folder changes nothing.
        second = external_library.scan_library(lib.id)
        assert second["added"] == 0
        assert second["removed"] == 0
        assert second["updated"] == 0
