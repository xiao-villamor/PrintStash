"""End-to-end / real-use-case tests for NAS folder mirroring (external libraries).

These complement the focused unit tests in ``test_external_libraries.py`` and
exercise the safety invariants and full workflows that decide whether the
feature is production-safe:

* **NAS bytes are sacred** — neither trash hard-delete nor the orphan-blob GC
  may ever touch a user's original files (only vault-owned thumbnails/rows go).
* **Write-back never overwrites** an existing file on the NAS.
* Revisions follow their model back into its library.
* Real folder shapes: mixed mesh + g-code, deep nested folders → collection
  hierarchy, SINGLE vs MIRROR collection modes, files moved within the share.
* Reconcile resilience: an mtime-only touch is a no-op; one bad file does not
  abort the whole scan.
* The periodic scheduler picks the right libraries, and the public API drives a
  background scan to completion.

A real on-disk folder of open-source models/g-codes can be pointed at via the
``PRINTSTASH_TEST_NAS_DIR`` env var to run ``test_scan_real_world_folder``.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    File,
    FileType,
    Model,
)
from app.db.scopes import live
from app.services import external_library, runtime_config, taxonomy, trash
from app.services.ingestion import add_gcode_revision_to_model, ingest_orca_gcode
from app.services.jobs import registry

FIXTURE_GCODE = Path(__file__).parent / "fixtures" / "sample.gcode"

# A small but valid ASCII-STL cube (a real mesh trimesh can parse + thumbnail).
_CUBE_STL = b"""solid cube
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 1 1 0
vertex 1 0 0
endloop
endfacet
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 1 0
vertex 1 1 0
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 1
vertex 1 0 1
vertex 1 1 1
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 1
vertex 1 1 1
vertex 0 1 1
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 1 0 1
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 1 0 1
vertex 0 0 1
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 1 0 0
vertex 1 1 0
vertex 1 1 1
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 1 0 0
vertex 1 1 1
vertex 1 0 1
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 1 1 0
vertex 0 1 0
vertex 0 1 1
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 1 1 0
vertex 0 1 1
vertex 1 1 1
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 1 0
vertex 0 0 0
vertex 0 0 1
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 1 0
vertex 0 0 1
vertex 0 1 1
endloop
endfacet
endsolid cube
"""


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


def _enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


def _make_library(session: Session, root: Path, **kw) -> ExternalLibrary:
    lib = ExternalLibrary(name="nas", root_path=str(root), **kw)
    session.add(lib)
    session.commit()
    session.refresh(lib)
    return lib


def _gcode_bytes(marker: str = "") -> bytes:
    """Fixture g-code plus a unique trailer so each marker hashes distinctly
    (a distinct sha256 → a distinct deduplicated Model)."""
    base = FIXTURE_GCODE.read_bytes()
    return base + f"\n; unique-marker {marker}\nG1 X0 Y0\n".encode() if marker else base


def _drop_gcode(dest_dir: Path, name: str, marker: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(_gcode_bytes(marker or ""))
    return target


def _drop_stl(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(_CUBE_STL)
    return target


def _stage(name: str, data: bytes) -> Path:
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}-{name}"
    staged.write_bytes(data)
    return staged


def _external_files(session: Session, *, live_only: bool = True) -> list[File]:
    stmt = select(File).where(File.is_external == True)  # noqa: E712
    if live_only:
        stmt = stmt.where(live(File))
    session.expire_all()
    return list(session.exec(stmt).all())


# --------------------------------------------------------------------------- #
# Safety invariant: the user's NAS bytes are never destroyed
# --------------------------------------------------------------------------- #
def test_hard_delete_model_never_destroys_nas_bytes(
    tmp_path: Path, db_session: Session
) -> None:
    """Trash retention purge (hard delete) removes DB rows + vault thumbnails but
    must leave the original file on the NAS completely untouched."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "precious.gcode", marker="keep-me")
    original_bytes = path.read_bytes()
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    f = _external_files(db_session)[0]
    model = db_session.get(Model, f.model_id)

    trash.soft_delete_model(db_session, model)
    trash.hard_delete_model(db_session, model)
    db_session.commit()

    # DB rows are gone...
    assert db_session.get(File, f.id) is None
    assert db_session.get(Model, model.id) is None
    # ...but the NAS file and its exact bytes survive.
    assert path.exists()
    assert path.read_bytes() == original_bytes


def test_orphan_gc_never_deletes_external_blobs(
    tmp_path: Path, db_session: Session
) -> None:
    """The hourly orphan-blob sweep walks vault storage only. External files live
    outside the vault and must never be swept, even with retention at zero."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "onnas.gcode", marker="gc")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    trash.gc_soft_deleted(retention_days=0)

    assert path.exists()
    assert path.read_bytes() == _gcode_bytes("gc")
    # The index row is still live (the file was never trashed).
    assert len(_external_files(db_session)) == 1


def test_write_back_never_overwrites_existing_nas_file(
    tmp_path: Path, db_session: Session
) -> None:
    """A web upload routed into the NAS must not clobber a same-named file the
    user already has there — it lands under a collision-safe name instead."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir(parents=True)
    precious = nas / "part.gcode"
    precious.write_bytes(b"; HAND-PLACED USER FILE - do not touch\n")
    lib = _make_library(
        db_session, nas, collection_mode=ExternalLibraryCollectionMode.MIRROR
    )

    staged = _stage("part.gcode", _gcode_bytes("upload"))
    ingest_orca_gcode(
        job_id="job-collision",
        staged_path=staged,
        original_filename="part.gcode",
        model_name="Part",
        collection=None,
        tags=None,
        source_hash=None,
        target_library_id=lib.id,
    )

    # Original bytes untouched.
    assert precious.read_bytes() == b"; HAND-PLACED USER FILE - do not touch\n"
    # New upload written beside it under a non-clobbering name.
    f = _external_files(db_session, live_only=False)[0]
    assert Path(f.path).name == "part-2.gcode"
    assert Path(f.path).exists()


# --------------------------------------------------------------------------- #
# Real folder shapes
# --------------------------------------------------------------------------- #
def test_scan_indexes_mixed_mesh_and_gcode(
    tmp_path: Path, db_session: Session
) -> None:
    """A realistic folder mixes meshes and slicer output; both index in place."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_stl(nas, "bracket.stl")
    _drop_gcode(nas, "bracket.gcode", marker="g")
    lib = _make_library(db_session, nas)

    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 2
    assert summary["aborted"] is False
    files = _external_files(db_session)
    types = {f.file_type for f in files}
    assert types == {FileType.STL, FileType.GCODE}
    for f in files:
        assert f.path.startswith(str(nas))  # indexed where it lives
        assert Path(f.path).exists()
        assert f.size_bytes > 0


def test_scan_indexes_but_skips_over_cap_mesh(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pathological dense file (the issue #29 case) must not OOM or abort the
    scan: it is still indexed in place, but its mesh is never loaded, so geometry
    is empty and the scan completes green."""
    trimesh = pytest.importorskip("trimesh")
    from app.db.models import Metadata

    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=10.0)  # 5120 triangles
    (nas / "dense.stl").write_bytes(mesh.export(file_type="stl"))
    # Force the file over the triangle cap so the real guard fires on a real file.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", len(mesh.faces) // 2)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    lib = _make_library(db_session, nas)

    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 1
    assert summary["aborted"] is False
    assert summary["errors"] == []
    db_session.refresh(lib)
    assert lib.last_scan_status == ExternalLibraryScanStatus.OK
    # Indexed in place, but the over-cap mesh was never loaded → no geometry.
    files = _external_files(db_session)
    assert len(files) == 1
    md = db_session.exec(
        select(Metadata).where(Metadata.file_id == files[0].id)
    ).first()
    assert md is None or md.triangle_count is None


def test_deep_nested_folders_build_collection_hierarchy(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas / "mechanical" / "brackets" / "v2", "corner.gcode", marker="deep")
    lib = _make_library(
        db_session, nas, collection_mode=ExternalLibraryCollectionMode.MIRROR
    )

    external_library.scan_library(lib.id)

    f = _external_files(db_session)[0]
    model = db_session.get(Model, f.model_id)
    assert model.collection_rel is not None
    assert model.collection_rel.path == "mechanical/brackets/v2"


def test_single_collection_mode_ignores_folder_structure(
    tmp_path: Path, db_session: Session
) -> None:
    """SINGLE mode dumps every scanned file into one configured collection,
    regardless of where it sits in the folder tree."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    coll = taxonomy.resolve_or_create_collection(db_session, "nas-dump")
    db_session.commit()
    db_session.refresh(coll)

    nas = tmp_path / "nas"
    _drop_gcode(nas / "sub-a", "one.gcode", marker="a")
    _drop_gcode(nas / "sub-b" / "deeper", "two.gcode", marker="b")
    lib = _make_library(
        db_session,
        nas,
        collection_mode=ExternalLibraryCollectionMode.SINGLE,
        target_collection_id=coll.id,
    )

    external_library.scan_library(lib.id)

    files = _external_files(db_session)
    assert len(files) == 2
    for f in files:
        model = db_session.get(Model, f.model_id)
        assert model.collection_rel is not None
        assert model.collection_rel.path == "nas-dump"


def test_file_moved_within_nas_is_reconciled(
    tmp_path: Path, db_session: Session
) -> None:
    """Moving a file to another subfolder reads as remove(old) + add(new): the
    index follows the file to its new path without leaving a stale live row."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    old_path = _drop_gcode(nas / "incoming", "widget.gcode", marker="move")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)
    assert len(_external_files(db_session)) == 1

    new_dir = nas / "sorted"
    new_dir.mkdir(parents=True)
    shutil.move(str(old_path), str(new_dir / "widget.gcode"))

    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 1
    assert summary["removed"] == 1
    live_files = _external_files(db_session)
    assert len(live_files) == 1
    assert live_files[0].path == str(new_dir / "widget.gcode")


def test_mtime_touch_without_content_change_is_skipped(
    tmp_path: Path, db_session: Session
) -> None:
    """A backup tool that rewrites mtimes but not bytes must not trigger a
    needless re-import — we just record the new signature."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "stable.gcode", marker="touch")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)
    before = _external_files(db_session)[0]
    old_sha = before.sha256

    future = path.stat().st_mtime + 5000.0
    os.utime(path, (future, future))

    summary = external_library.scan_library(lib.id)

    assert summary["updated"] == 0
    assert summary["skipped"] == 1
    db_session.expire_all()  # scan committed via its own session
    after = db_session.get(File, before.id)
    assert after.sha256 == old_sha  # content unchanged
    assert after.source_mtime == pytest.approx(future)  # signature refreshed


def test_per_file_error_is_isolated_and_scan_continues(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unparseable/locked file must not abort the whole NAS sync: it is
    recorded in ``errors`` while the rest of the folder still indexes."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "good-1.gcode", marker="g1")
    _drop_gcode(nas, "bad.gcode", marker="g2")
    _drop_gcode(nas, "good-2.gcode", marker="g3")
    lib = _make_library(db_session, nas)

    real_index = external_library._index_external_file

    def flaky(session, library, source_path, size, mtime):  # type: ignore[no-untyped-def]
        if source_path.name == "bad.gcode":
            raise RuntimeError("simulated parse failure")
        return real_index(session, library, source_path, size, mtime)

    monkeypatch.setattr(external_library, "_index_external_file", flaky)

    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 2
    assert summary["aborted"] is False
    assert len(summary["errors"]) == 1
    assert "bad.gcode" in summary["errors"][0]
    db_session.refresh(lib)
    # A completed-with-failures scan is PARTIAL, not a misleading green OK.
    assert lib.last_scan_status == ExternalLibraryScanStatus.PARTIAL


def test_clean_scan_reports_ok(tmp_path: Path, db_session: Session) -> None:
    """A scan with no per-file failures stays OK (PARTIAL is only for errors)."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "good.gcode", marker="ok")
    lib = _make_library(db_session, nas)

    external_library.scan_library(lib.id)

    db_session.refresh(lib)
    assert lib.last_scan_status == ExternalLibraryScanStatus.OK


def test_unexpected_failure_never_strands_scan_running(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception outside the per-file boundary (e.g. a NAS mount dropping
    mid-walk) must land the library in a terminal ERROR state, not leave it
    stranded RUNNING where the scheduler would skip it forever (#24 follow-up)."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "any.gcode", marker="x")
    lib = _make_library(db_session, nas)

    def boom(_root):  # the walk itself fails, outside any per-file try/except
        raise OSError("transport endpoint is not connected")

    monkeypatch.setattr(external_library, "_walk", boom)

    summary = external_library.scan_library(lib.id)

    assert summary["aborted"] is True
    assert "scan_failed" in summary["error"]
    db_session.refresh(lib)
    assert lib.last_scan_status == ExternalLibraryScanStatus.ERROR
    # last_scanned_at is stamped so the scheduler treats it as due again (a
    # terminal state), rather than a permanently-skipped RUNNING row.
    assert lib.last_scanned_at is not None


def test_mtime_jitter_within_tolerance_skips_rehash(
    tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sub-second/FAT-granularity mtime drift on unchanged content takes the
    cheap skip path — it must not trigger a full sha256 re-hash over the NAS."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "stable.gcode", marker="jitter")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    # Nudge mtime by less than the tolerance (content identical).
    jittered = path.stat().st_mtime + 1.0
    os.utime(path, (jittered, jittered))

    calls = {"n": 0}
    real_hash = external_library.sha256_file

    def counting_hash(p):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_hash(p)

    monkeypatch.setattr(external_library, "sha256_file", counting_hash)

    summary = external_library.scan_library(lib.id)

    assert summary["skipped"] == 1
    assert summary["updated"] == 0
    assert calls["n"] == 0  # no re-hash storm for jitter under the tolerance


# --------------------------------------------------------------------------- #
# Revisions follow the model back into its library
# --------------------------------------------------------------------------- #
def test_revision_is_written_back_into_library(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "bracket.gcode", marker="v1")
    lib = _make_library(
        db_session, nas, collection_mode=ExternalLibraryCollectionMode.MIRROR
    )
    external_library.scan_library(lib.id)
    model = db_session.get(Model, _external_files(db_session)[0].model_id)

    staged = _stage("bracket-v2.gcode", _gcode_bytes("v2"))
    rev = add_gcode_revision_to_model(
        session=db_session,
        model=model,
        staged_path=staged,
        original_filename="bracket-v2.gcode",
        revision_label="v2",
        revision_status=None,
        revision_notes=None,
        is_recommended=False,
    )

    assert rev.is_external is True
    assert rev.external_library_id == lib.id
    assert rev.path.startswith(str(nas))
    assert Path(rev.path).exists()
    assert not staged.exists()  # staged upload moved onto the NAS, not copied

    # The next scan recognises the written-back revision as already-indexed.
    summary = external_library.scan_library(lib.id)
    assert summary["added"] == 0


# --------------------------------------------------------------------------- #
# Library lifecycle via the public API
# --------------------------------------------------------------------------- #
def test_delete_library_via_api_trashes_index_but_keeps_nas_bytes(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    p1 = _drop_gcode(nas, "a.gcode", marker="a")
    p2 = _drop_gcode(nas, "b.gcode", marker="b")
    lib = _make_library(db_session, nas)
    lib_id = lib.id
    external_library.scan_library(lib_id)
    assert len(_external_files(db_session)) == 2

    resp = client.delete(f"/api/v1/libraries/{lib_id}", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["files_trashed"] == 2
    # Index rows trashed, library row gone...
    assert _external_files(db_session) == []
    db_session.expunge_all()
    assert db_session.get(ExternalLibrary, lib_id) is None
    # ...NAS files untouched.
    assert p1.exists() and p2.exists()


def test_scan_via_api_runs_background_job_to_completion(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    """Full round trip: create a library over HTTP, trigger a scan, and confirm
    the background job completes and the folder is indexed."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas / "parts", "alpha.gcode", marker="a")
    _drop_gcode(nas, "beta.gcode", marker="b")

    created = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "nas", "root_path": str(nas)},
    )
    assert created.status_code == 201, created.text
    lib_id = created.json()["id"]

    scan = client.post(f"/api/v1/libraries/{lib_id}/scan", headers=auth_headers)
    assert scan.status_code == 202, scan.text
    job_id = scan.json()["job_id"]

    # TestClient drains background tasks before returning, so the job is done.
    job = registry.get(job_id)
    assert job is not None
    assert job.state == "completed"
    assert job.result["added"] == 2

    files = _external_files(db_session)
    assert len(files) == 2
    assert all(f.external_library_id == lib_id for f in files)
    # Persisted scan summary is surfaced on the library read model.
    listed = client.get("/api/v1/libraries", headers=auth_headers).json()
    assert listed[0]["last_scan_status"] == "ok"
    assert listed[0]["last_scan_summary"]["added"] == 2


# --------------------------------------------------------------------------- #
# Periodic scheduler selection
# --------------------------------------------------------------------------- #
def test_scheduler_selects_only_due_libraries(
    tmp_path: Path, db_session: Session
) -> None:
    _enable_feature(db_session)
    now = utcnow()

    never = _make_library(db_session, tmp_path / "never", enabled=True)
    manual = _make_library(
        db_session,
        tmp_path / "manual",
        enabled=True,
        scan_schedule="",  # manual only → never auto-due
        last_scanned_at=now - timedelta(hours=2),
    )
    stale = _make_library(
        db_session,
        tmp_path / "stale",
        enabled=True,
        scan_schedule="0 * * * *",  # hourly; 2h elapsed → a boundary has passed
        last_scanned_at=now - timedelta(hours=2),
    )
    disabled = _make_library(db_session, tmp_path / "disabled", enabled=False)
    running = _make_library(
        db_session,
        tmp_path / "running",
        enabled=True,
        last_scan_status=ExternalLibraryScanStatus.RUNNING,
    )

    due = external_library.libraries_due_for_scan(db_session)

    assert never.id in due  # never scanned → due immediately
    assert stale.id in due  # cron boundary elapsed → due
    assert manual.id not in due  # manual only → never auto-due
    assert disabled.id not in due  # disabled → never
    assert running.id not in due  # already scanning → skipped


# --------------------------------------------------------------------------- #
# Real folder of open-source models/g-codes
# --------------------------------------------------------------------------- #
# Defaults to the repo's ``testdata/`` folder; override with PRINTSTASH_TEST_NAS_DIR.
_REPO_TESTDATA = Path(__file__).resolve().parents[2] / "testdata"


def _real_nas_dir() -> Path | None:
    env = os.environ.get("PRINTSTASH_TEST_NAS_DIR")
    if env:
        return Path(env)
    return _REPO_TESTDATA if _REPO_TESTDATA.is_dir() else None


def _supported_files(root: Path) -> list[Path]:
    """Files under *root* the scanner recognises (mirrors ``_walk``'s filter)."""
    from app.db.models import SUFFIX_TO_FILE_TYPE

    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUFFIX_TO_FILE_TYPE
    ]


@pytest.mark.skipif(
    _real_nas_dir() is None,
    reason="no testdata/ folder and PRINTSTASH_TEST_NAS_DIR unset",
)
def test_scan_real_world_folder(tmp_path: Path, db_session: Session) -> None:
    """Scan the engine against real STL/3MF/OBJ/g-code files (repo ``testdata/``).

    Every supported file (including PrusaSlicer binary ``.bgcode``) must index in
    place without a parse error, point at a real non-empty on-disk path, and an
    immediate rescan must be a clean no-op. Unsupported files are silently
    ignored, never errored.
    """
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    root = _real_nas_dir()
    assert root is not None
    expected = _supported_files(root)
    assert expected, f"no supported model/g-code files found under {root}"

    lib = _make_library(db_session, root)
    summary = external_library.scan_library(lib.id)

    assert summary["aborted"] is False
    # Every supported file indexed, and no real file tripped a parse/ingest error.
    assert summary["errors"] == [], summary["errors"]
    assert summary["added"] == len(expected)

    files = _external_files(db_session)
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
