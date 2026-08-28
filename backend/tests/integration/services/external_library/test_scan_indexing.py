"""`scan_library` turning a folder on somebody else's NAS into library rows.

Indexing is the half of the feature a user sees: point PrintStash at a share and
the models in it appear beside the vault, organised the way the folders already
organise them. The behaviours here are the ones that decide whether that
impression is true — every recognised suffix is picked up (a scan that indexed
g-code but skipped meshes would look like a half-empty share), the folder
hierarchy becomes the collection hierarchy in MIRROR mode and is deliberately
flattened in SINGLE mode, and a mesh past the configured cap is indexed as a row
without being loaded, because the alternative is one oversized file aborting the
whole scan.

None of this owns the bytes. The assertions check index rows and collection
paths; the files stay where the user put them, which is what the sibling
`test_nas_byte_safety.py` pins."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import (
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    File,
    FileType,
    Metadata,
    Model,
)
from app.db.scopes import live
from app.services import external_library, taxonomy
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    drop_gcode,
    drop_stl,
    enable_feature,
    external_files,
)


class TestScanLibrary:
    def test_a_scan_mirrors_the_directory_tree_as_collections(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas / "functional", "bracket.gcode")
        drop_gcode(nas, "loose.gcode")
        lib = build_external_library(db_session, nas, name="nas")

        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 2
        assert summary["removed"] == 0
        files = db_session.exec(select(File).where(live(File))).all()
        files = [f for f in files if f.is_external]
        assert len(files) == 2
        for f in files:
            assert f.is_external is True
            assert f.external_library_id == lib.id
            # Path points at the NAS file, not vault data_dir.
            assert f.path.startswith(str(nas))
            assert str(_overlay["data_dir"]) not in f.path
            assert f.source_mtime is not None

        bracket = next(f for f in files if f.original_filename == "bracket.gcode")
        model = db_session.get(Model, bracket.model_id)
        assert model.collection_rel is not None
        assert model.collection_rel.path == "functional"

    def test_a_scan_indexes_every_supported_file_type(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """A realistic folder mixes meshes and slicer output; both index in place."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_stl(nas, "bracket.stl")
        drop_gcode(nas, "bracket.gcode", marker="g")
        lib = build_external_library(db_session, nas, name="nas")

        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 2
        assert summary["aborted"] is False
        files = external_files(db_session)
        types = {f.file_type for f in files}
        assert types == {FileType.STL, FileType.GCODE}
        for f in files:
            assert f.path.startswith(str(nas))  # indexed where it lives
            assert Path(f.path).exists()
            assert f.size_bytes > 0

    def test_deep_nested_folders_build_collection_hierarchy(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(
            nas / "mechanical" / "brackets" / "v2", "corner.gcode", marker="deep"
        )
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

        external_library.scan_library(lib.id)

        f = external_files(db_session)[0]
        model = db_session.get(Model, f.model_id)
        assert model.collection_rel is not None
        assert model.collection_rel.path == "mechanical/brackets/v2"

    def test_single_collection_mode_ignores_folder_structure(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """SINGLE mode dumps every scanned file into one configured collection,
        regardless of where it sits in the folder tree."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        coll = taxonomy.resolve_or_create_collection(db_session, "nas-dump")
        db_session.commit()
        db_session.refresh(coll)

        nas = tmp_path / "nas"
        drop_gcode(nas / "sub-a", "one.gcode", marker="a")
        drop_gcode(nas / "sub-b" / "deeper", "two.gcode", marker="b")
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.SINGLE,
            target_collection_id=coll.id,
        )

        external_library.scan_library(lib.id)

        files = external_files(db_session)
        assert len(files) == 2
        for f in files:
            model = db_session.get(Model, f.model_id)
            assert model.collection_rel is not None
            assert model.collection_rel.path == "nas-dump"

    def test_scan_indexes_but_skips_over_cap_mesh(
        self, tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pathological dense file must avoid Trimesh without losing its preview.

        The scan indexes it in place and the bounded STL fallback provides geometry
        and a thumbnail, so the scan completes without an OOM or a partial result.
        """
        trimesh = pytest.importorskip("trimesh")

        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir(parents=True, exist_ok=True)
        mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)  # 5120 triangles
        (nas / "dense.stl").write_bytes(mesh.export(file_type="stl"))
        # Force the file over the triangle cap so the real guard fires on a real file.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", len(mesh.faces) // 2)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        lib = build_external_library(db_session, nas, name="nas")

        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 1
        assert summary["aborted"] is False
        assert summary["errors"] == []
        db_session.refresh(lib)
        assert lib.last_scan_status == ExternalLibraryScanStatus.OK
        # Indexed in place; the over-cap mesh was never loaded through Trimesh, but
        # the streaming fallback still publishes geometry and a thumbnail.
        files = external_files(db_session)
        assert len(files) == 1
        md = db_session.exec(
            select(Metadata).where(Metadata.file_id == files[0].id)
        ).first()
        assert md is not None
        assert md.triangle_count == len(mesh.faces)
        model = db_session.get(Model, files[0].model_id)
        assert model is not None
        assert model.thumbnail_path is not None
        assert Path(model.thumbnail_path).is_file()

    def test_clean_scan_reports_ok(self, tmp_path: Path, db_session: Session) -> None:
        """A scan with no per-file failures stays OK (PARTIAL is only for errors)."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "good.gcode", marker="ok")
        lib = build_external_library(db_session, nas, name="nas")

        external_library.scan_library(lib.id)

        db_session.refresh(lib)
        assert lib.last_scan_status == ExternalLibraryScanStatus.OK
