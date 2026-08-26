"""Defends scan via api runs background job to completion at the external libraries API integration boundary.

A regression could make the indexed library diverge from its configured filesystem.
"""

from __future__ import annotations

from ._external_libraries_integration_shared import (
    ExternalLibraryScanStatus,
    Model,
    Path,
    Session,
    _configure_storage,
    _drop_gcode,
    _enable_feature,
    _external_files,
    _make_library,
    _real_nas_dir,
    _supported_files,
    external_library,
    pytest,
    registry,
    timedelta,
    utcnow,
)


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
