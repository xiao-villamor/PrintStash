"""External library (NAS folder mirroring) scan + write-back tests."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    File,
    Model,
)
from app.db.scopes import live
from app.services import external_library, runtime_config
from app.services.ingestion import ingest_orca_gcode

FIXTURE_GCODE = Path(__file__).parent / "fixtures" / "sample.gcode"


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
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


def _drop_gcode(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    shutil.copy(FIXTURE_GCODE, target)
    return target


def test_scan_indexes_new_files_and_mirrors_collections(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas / "functional", "bracket.gcode")
    _drop_gcode(nas, "loose.gcode")
    lib = _make_library(db_session, nas)

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


def test_rescan_is_idempotent(tmp_path: Path, db_session: Session) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "a.gcode")
    lib = _make_library(db_session, nas)

    external_library.scan_library(lib.id)
    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 0
    assert summary["skipped"] == 1
    assert summary["updated"] == 0


def test_changed_content_reindexes_same_row(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "a.gcode")
    lib = _make_library(db_session, nas)

    external_library.scan_library(lib.id)
    before = db_session.exec(select(File).where(File.is_external == True)).all()  # noqa: E712
    assert len(before) == 1
    old_sha = before[0].sha256

    # Append bytes so size + content differ.
    with path.open("ab") as fh:
        fh.write(b"\n; mutated\nG1 X1 Y1\n")

    summary = external_library.scan_library(lib.id)

    assert summary["updated"] == 1
    assert summary["added"] == 0
    after = db_session.exec(select(File).where(File.is_external == True)).all()  # noqa: E712
    assert len(after) == 1  # same row, no duplicate / new version
    db_session.refresh(after[0])
    assert after[0].sha256 != old_sha


def test_removed_file_is_trashed_and_model_soft_deleted(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "gone.gcode")
    # A distinct-content file so the folder stays non-empty (not an "unmount")
    # and dedup keeps it under a *different* model than gone.gcode.
    stays = _drop_gcode(nas, "stays.gcode")
    with stays.open("ab") as fh:
        fh.write(b"\n; distinct content\nG1 X5 Y5\n")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    f = db_session.exec(
        select(File).where(File.original_filename == "gone.gcode")
    ).first()
    model_id = f.model_id
    path.unlink()

    summary = external_library.scan_library(lib.id)

    assert summary["removed"] == 1
    db_session.expire_all()
    f2 = db_session.get(File, f.id)
    assert f2.deleted_at is not None  # soft-deleted, not hard-deleted
    model = db_session.get(Model, model_id)
    assert model.deleted_at is not None


def test_unmounted_root_aborts_without_deleting(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "keep.gcode")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    # Simulate an unmount: the whole root disappears.
    shutil.rmtree(nas)

    summary = external_library.scan_library(lib.id)

    assert summary["aborted"] is True
    assert summary["removed"] == 0
    # Nothing was trashed.
    live_files = db_session.exec(
        select(File).where(File.is_external == True, live(File))  # noqa: E712
    ).all()
    assert len(live_files) == 1
    db_session.refresh(lib)
    assert lib.last_scan_status == ExternalLibraryScanStatus.ERROR


def test_empty_root_with_indexed_files_aborts(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    path = _drop_gcode(nas, "keep.gcode")
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    # Root still exists but is empty (e.g. NAS share mounted but unpopulated).
    path.unlink()

    summary = external_library.scan_library(lib.id)

    assert summary["aborted"] is True
    live_files = db_session.exec(
        select(File).where(File.is_external == True, live(File))  # noqa: E712
    ).all()
    assert len(live_files) == 1


def test_write_back_lands_in_nas_folder(tmp_path: Path, db_session: Session) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir(parents=True)
    lib = _make_library(
        db_session, nas, collection_mode=ExternalLibraryCollectionMode.MIRROR
    )

    # Stage an upload and route it into the library (write-back).
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}.gcode"
    shutil.copy(FIXTURE_GCODE, staged)
    ingest_orca_gcode(
        job_id="job-wb",
        staged_path=staged,
        original_filename="written.gcode",
        model_name="Written Model",
        collection="cool/widgets",
        tags=None,
        source_hash=None,
        target_library_id=lib.id,
    )

    f = db_session.exec(select(File).where(File.is_external == True)).first()  # noqa: E712
    assert f is not None
    assert f.external_library_id == lib.id
    # Physically written under the library root, mirrored into the collection path.
    assert f.path.startswith(str(nas))
    assert Path(f.path).exists()
    assert "cool/widgets" in f.path.replace("\\", "/")
    assert not staged.exists()  # staged upload was moved, not left behind

    # A subsequent scan recognises the written file as unchanged.
    summary = external_library.scan_library(lib.id)
    assert summary["added"] == 0
    assert summary["skipped"] == 1


def test_api_gated_when_feature_disabled(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    nas = tmp_path / "nas"
    nas.mkdir(parents=True)
    # Feature OFF by default → endpoints respond feature_disabled.
    resp = client.get("/api/v1/libraries", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "feature_disabled"

    create = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "nas", "root_path": str(nas)},
    )
    assert create.status_code == 404


def test_api_crud_and_path_validation(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir(parents=True)

    # Non-existent path is rejected.
    bad = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "x", "root_path": str(tmp_path / "missing")},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "root_path_not_a_directory"

    # Invalid cron is rejected.
    bad_cron = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "x", "root_path": str(nas), "scan_schedule": "not a cron"},
    )
    assert bad_cron.status_code == 400
    assert bad_cron.json()["detail"] == "invalid_cron_schedule"

    created = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={
            "name": "nas",
            "root_path": str(nas),
            "scan_schedule": "0 */6 * * *",
            "watch_mode": "off",
        },
    )
    assert created.status_code == 201, created.text
    lib_id = created.json()["id"]
    assert created.json()["collection_mode"] == "mirror"
    assert created.json()["scan_schedule"] == "0 */6 * * *"
    assert created.json()["watch_mode"] == "off"
    # fs_kind is detected on create; watching is off so it's inactive.
    assert created.json()["fs_kind"] in {"local", "network", "unknown"}
    assert created.json()["watch_active"] is False

    listed = client.get("/api/v1/libraries", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    patched = client.patch(
        f"/api/v1/libraries/{lib_id}",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/libraries/{lib_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/v1/libraries", headers=auth_headers).json() == []


def test_is_due_cron_logic() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 15, 12, 30, tzinfo=timezone.utc)
    hourly = "0 * * * *"

    # Never scanned + valid schedule → due.
    assert external_library.is_due(hourly, None, now) is True
    # Last scan was last hour, a boundary (12:00) has passed → due.
    assert external_library.is_due(hourly, datetime(2026, 6, 15, 11, 0, tzinfo=timezone.utc), now) is True
    # Last scan was 10 min ago, no new boundary since → not due.
    assert external_library.is_due(hourly, datetime(2026, 6, 15, 12, 20, tzinfo=timezone.utc), now) is False
    # Empty schedule = manual only → never due.
    assert external_library.is_due("", None, now) is False
    # Invalid cron → never due (defensive).
    assert external_library.is_due("nope", None, now) is False


def test_watch_mode_persisted_as_enum_name(tmp_path: Path, db_session: Session) -> None:
    """Guard the storage contract: SQLAlchemy persists the enum *name* ("AUTO"),
    so the migration's server_default must match — storing the lowercase value
    ("auto") makes reads raise LookupError and 500 the libraries listing."""
    from sqlalchemy import text

    from app.db.models import ExternalLibraryWatchMode as WM

    nas = tmp_path / "nas"
    nas.mkdir()
    lib = _make_library(db_session, nas, watch_mode=WM.AUTO)
    raw = db_session.execute(
        text("SELECT watch_mode FROM external_libraries WHERE id = :id"),
        {"id": lib.id},
    ).scalar_one()
    assert raw == "AUTO"  # stored by NAME, not the "auto" value


def test_detect_fs_kind_and_should_watch(tmp_path: Path) -> None:
    from app.db.models import ExternalLibrary, ExternalLibraryWatchMode as WM

    # tmp_path is a local filesystem on the test host.
    assert external_library.detect_fs_kind(tmp_path) in {"local", "unknown"}

    lib = ExternalLibrary(name="x", root_path=str(tmp_path))

    # AUTO watches only local filesystems.
    lib.watch_mode = WM.AUTO
    assert external_library.should_watch(lib, "local") is True
    assert external_library.should_watch(lib, "network") is False
    assert external_library.should_watch(lib, "unknown") is False
    # EVENTS forces watching regardless of fs; OFF never watches.
    lib.watch_mode = WM.EVENTS
    assert external_library.should_watch(lib, "network") is True
    lib.watch_mode = WM.OFF
    assert external_library.should_watch(lib, "local") is False
    # Disabled libraries are never watched.
    lib.watch_mode = WM.EVENTS
    lib.enabled = False
    assert external_library.should_watch(lib, "local") is False


def test_feature_disabled_keeps_uploads_in_vault(
    tmp_path: Path, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    # Feature OFF (default). Even with a target_library_id the blob stays in vault.
    nas = tmp_path / "nas"
    nas.mkdir(parents=True)
    lib = _make_library(db_session, nas)

    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}.gcode"
    shutil.copy(FIXTURE_GCODE, staged)
    ingest_orca_gcode(
        job_id="job-vault",
        staged_path=staged,
        original_filename="vaulted.gcode",
        model_name="Vaulted",
        collection=None,
        tags=None,
        source_hash=None,
        target_library_id=lib.id,
    )

    f = db_session.exec(
        select(File).where(File.original_filename == "vaulted.gcode")
    ).first()
    assert f is not None
    assert f.is_external is False
    assert f.path.startswith(str(_overlay["data_dir"]))
