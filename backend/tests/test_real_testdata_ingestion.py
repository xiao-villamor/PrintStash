"""Ingestion / revision coverage driven by *real* model & g-code files.

These exercise the production pipelines end-to-end against the open-source
files in the repo ``testdata/`` folder (real STL/3MF meshes and real slicer
g-code), so a regression in mesh geometry extraction, slicer-metadata parsing,
embedded-thumbnail handling, dedup, or revision bookkeeping fails loudly here.

Tests skip automatically when a given file is absent, so the folder can grow or
shrink without breaking the suite.

Safety: ingestion *moves* the staged blob into vault storage, so every test
stages a **copy** of the real file — the originals under ``testdata/`` are never
moved, overwritten, or deleted.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import File, FileType, Metadata, Model
from app.db.scopes import live
from app.services.ingestion import (
    add_gcode_revision_to_model,
    ingest_mesh,
    ingest_orca_gcode,
)
from app.services.jobs import registry

# --------------------------------------------------------------------------- #
# Real fixtures
# --------------------------------------------------------------------------- #
TESTDATA = Path(__file__).resolve().parents[2] / "testdata"

CUBE_STL = TESTDATA / "Calibration Cube.stl"
CUBE_GCODE = TESTDATA / "Calibration Cube_PLA_19m6s.gcode"
SPATULA_3MF = TESTDATA / "Spatula_Printables_IS.3mf"
SPATULA_GCODE = TESTDATA / "Spatula_Printables_0.4n_0.15mm_PLA_MK4IS_MK3.9IS_27m.gcode"
BENCHY_STL = TESTDATA / "benchy" / "3dbenchy.stl"
BENCHY_GCODE_A = TESTDATA / "benchy" / "3dbenchy_PLA_1h12m.gcode"
BENCHY_GCODE_B = TESTDATA / "benchy" / "3dbenchy_PLA_1h13m.gcode"


def _requires(path: Path):
    return pytest.mark.skipif(not path.exists(), reason=f"missing real fixture {path}")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "thumbs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "staging" / "_incoming").mkdir(parents=True, exist_ok=True)


def _stage_copy(src: Path) -> Path:
    """Copy a real file into staging so ingestion can consume (move) it without
    touching the original under ``testdata/``."""
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}-{src.name}"
    shutil.copy(src, staged)
    return staged


def _metadata_for(session: Session, file_id: int) -> Metadata:
    md = session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
    assert md is not None
    return md


def _ingest_mesh(
    session: Session, src: Path, file_type: FileType, *, model_name: str
) -> tuple[Model, File]:
    job_id = registry.create()
    ingest_mesh(
        job_id=job_id,
        staged_path=_stage_copy(src),
        original_filename=src.name,
        model_name=model_name,
        file_type=file_type,
        collection=None,
        tags=None,
        source_hash=None,
    )
    job = registry.get(job_id)
    assert job is not None and job.state == "completed", getattr(job, "error", None)
    session.expire_all()
    return session.get(Model, job.model_id), session.get(File, job.file_id)


def _ingest_gcode(
    session: Session, src: Path, *, model_name: str, source_hash: str | None = None
) -> tuple[Model, File]:
    job_id = registry.create()
    ingest_orca_gcode(
        job_id=job_id,
        staged_path=_stage_copy(src),
        original_filename=src.name,
        model_name=model_name,
        collection=None,
        tags=None,
        source_hash=source_hash,
    )
    job = registry.get(job_id)
    assert job is not None and job.state == "completed", getattr(job, "error", None)
    session.expire_all()
    return session.get(Model, job.model_id), session.get(File, job.file_id)


# --------------------------------------------------------------------------- #
# Mesh ingestion — real STL / 3MF
# --------------------------------------------------------------------------- #
@_requires(CUBE_STL)
def test_ingest_real_stl_extracts_geometry_and_thumbnail(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, f = _ingest_mesh(db_session, CUBE_STL, FileType.STL, model_name="Calibration Cube")

    assert f.file_type == FileType.STL
    assert f.size_bytes == CUBE_STL.stat().st_size
    assert f.is_external is False
    assert f.path.startswith(str(_overlay["data_dir"]))  # copied into the vault

    md = _metadata_for(db_session, f.id)
    # A 20mm calibration cube, ~252 triangles.
    assert md.bbox_x_mm == pytest.approx(20.0, abs=0.1)
    assert md.bbox_y_mm == pytest.approx(20.0, abs=0.1)
    assert md.bbox_z_mm == pytest.approx(20.0, abs=0.1)
    assert md.triangle_count == 252
    assert md.volume_mm3 == pytest.approx(7837, rel=0.02)
    # A thumbnail was rendered and adopted by the model.
    assert model.thumbnail_path is not None
    assert model.thumbnail_file_id == f.id


@_requires(SPATULA_3MF)
def test_ingest_real_3mf_extracts_geometry(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, f = _ingest_mesh(db_session, SPATULA_3MF, FileType.THREE_MF, model_name="Spatula")

    assert f.file_type == FileType.THREE_MF
    md = _metadata_for(db_session, f.id)
    # A flat ~126x54mm spatula, thin in Z.
    assert md.bbox_x_mm == pytest.approx(126.0, abs=1.0)
    assert md.bbox_y_mm == pytest.approx(54.0, abs=1.0)
    assert md.bbox_z_mm == pytest.approx(4.43, abs=0.5)
    assert md.triangle_count and md.triangle_count > 1000
    assert model.thumbnail_path is not None


# --------------------------------------------------------------------------- #
# G-code ingestion — real slicer output
# --------------------------------------------------------------------------- #
@_requires(CUBE_GCODE)
def test_ingest_real_gcode_parses_slicer_metadata(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, f = _ingest_gcode(db_session, CUBE_GCODE, model_name="Calibration Cube GCode")

    assert f.file_type == FileType.GCODE
    # The very first g-code on a model always claims the recommended marker.
    assert f.is_recommended is True

    md = _metadata_for(db_session, f.id)
    assert md.printer_model == "Creality Ender-3 V3 SE"
    assert md.layer_height_mm == pytest.approx(0.2)
    assert md.nozzle_diameter_mm == pytest.approx(0.4)
    assert md.filament_weight_g == pytest.approx(4.61, abs=0.05)


@_requires(SPATULA_GCODE)
def test_ingest_real_prusa_gcode_extracts_embedded_thumbnail(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, f = _ingest_gcode(db_session, SPATULA_GCODE, model_name="Spatula GCode")

    md = _metadata_for(db_session, f.id)
    assert md.printer_model == "MK4IS"
    assert md.layer_height_mm == pytest.approx(0.15)
    assert md.filament_weight_g == pytest.approx(10.55, abs=0.05)
    # PrusaSlicer embeds a preview PNG; it should be extracted + adopted (as webp).
    assert model.thumbnail_path is not None
    assert model.thumbnail_file_id == f.id


# --------------------------------------------------------------------------- #
# Dedup — identical real bytes collapse to one model
# --------------------------------------------------------------------------- #
@_requires(CUBE_STL)
def test_reingesting_identical_real_file_dedups_to_one_model(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model_a, file_v1 = _ingest_mesh(db_session, CUBE_STL, FileType.STL, model_name="Cube")
    model_b, file_v2 = _ingest_mesh(db_session, CUBE_STL, FileType.STL, model_name="Cube Again")

    # Same content hash → same model; the re-upload is a new version, not a clone.
    assert model_a.id == model_b.id
    assert file_v1.sha256 == file_v2.sha256
    assert file_v1.version == 1
    assert file_v2.version == 2

    models = db_session.exec(select(Model).where(Model.hash == file_v1.sha256)).all()
    assert len(models) == 1
    files = db_session.exec(
        select(File).where(File.model_id == model_a.id, live(File))
    ).all()
    assert len(files) == 2


# --------------------------------------------------------------------------- #
# Revisions — real benchy g-code variants
# --------------------------------------------------------------------------- #
@_requires(BENCHY_GCODE_A)
@_requires(BENCHY_GCODE_B)
def test_real_gcode_revisions_version_and_keep_first_recommended(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, first = _ingest_gcode(db_session, BENCHY_GCODE_A, model_name="3DBenchy")
    assert first.is_recommended is True

    second = add_gcode_revision_to_model(
        session=db_session,
        model=model,
        staged_path=_stage_copy(BENCHY_GCODE_B),
        original_filename=BENCHY_GCODE_B.name,
        revision_label="1h13m variant",
        revision_status=None,
        revision_notes=None,
        is_recommended=False,
    )

    assert first.sha256 != second.sha256  # genuinely different slices
    assert second.version == 2
    assert second.revision_label == "1h13m variant"

    db_session.expire_all()
    gcode_files = db_session.exec(
        select(File).where(
            File.model_id == model.id,
            File.file_type == FileType.GCODE,
            live(File),
        )
    ).all()
    assert len(gcode_files) == 2
    # Exactly one recommended revision, and adding a new one did not steal it.
    recommended = [g for g in gcode_files if g.is_recommended]
    assert len(recommended) == 1
    assert recommended[0].id == first.id


@_requires(BENCHY_GCODE_A)
@_requires(BENCHY_GCODE_B)
def test_marking_new_real_revision_recommended_clears_previous(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    model, first = _ingest_gcode(db_session, BENCHY_GCODE_A, model_name="3DBenchy B")

    second = add_gcode_revision_to_model(
        session=db_session,
        model=model,
        staged_path=_stage_copy(BENCHY_GCODE_B),
        original_filename=BENCHY_GCODE_B.name,
        revision_label="faster",
        revision_status=None,
        revision_notes=None,
        is_recommended=True,
    )

    db_session.expire_all()
    refreshed_first = db_session.get(File, first.id)
    refreshed_second = db_session.get(File, second.id)
    # The new revision took the marker; the old one was cleared. Still exactly one.
    assert refreshed_second.is_recommended is True
    assert refreshed_first.is_recommended is False
