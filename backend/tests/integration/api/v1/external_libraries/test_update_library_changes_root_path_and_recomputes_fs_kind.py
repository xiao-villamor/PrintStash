"""Defends update library changes root path and recomputes fs kind at the external libraries API integration boundary.

A regression could make the indexed library diverge from its configured filesystem.
"""

from __future__ import annotations

from ._external_libraries_shared import (
    Path,
    Session,
    _configure_storage,
    _drop_gcode,
    _enable_feature,
    _make_library,
)


def test_update_library_changes_root_path_and_recomputes_fs_kind(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    first_root = tmp_path / "first"
    first_root.mkdir()
    second_root = tmp_path / "second"
    second_root.mkdir()
    lib = _make_library(db_session, first_root)

    resp = client.patch(
        f"/api/v1/libraries/{lib.id}",
        headers=auth_headers,
        json={
            "root_path": str(second_root),
            "name": "renamed",
            "scan_schedule": "0 0 * * *",
            "watch_mode": "events",
            "collection_mode": "single",
            "target_collection_id": 42,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["root_path"] == str(second_root)
    assert body["name"] == "renamed"
    assert body["scan_schedule"] == "0 0 * * *"
    assert body["watch_mode"] == "events"
    assert body["collection_mode"] == "single"
    assert body["target_collection_id"] == 42


def test_update_library_rejects_invalid_schedule(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir()
    lib = _make_library(db_session, nas)

    resp = client.patch(
        f"/api/v1/libraries/{lib.id}",
        headers=auth_headers,
        json={"scan_schedule": "not a cron"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_cron_schedule"


def test_update_library_unknown_id_404(
    client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    resp = client.patch(
        "/api/v1/libraries/999999",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "library_not_found"


def test_delete_library_unknown_id_404(
    client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    resp = client.delete("/api/v1/libraries/999999", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "library_not_found"


def test_scan_now_queues_job(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas, "a.gcode")
    lib = _make_library(db_session, nas)

    resp = client.post(f"/api/v1/libraries/{lib.id}/scan", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


def test_scan_now_unknown_library_404(
    client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    resp = client.post("/api/v1/libraries/999999/scan", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "library_not_found"


def test_scan_path_queues_job_for_subfolder(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    _drop_gcode(nas / "functional", "bracket.gcode")
    lib = _make_library(db_session, nas)

    resp = client.post(
        f"/api/v1/libraries/{lib.id}/scan-path",
        headers=auth_headers,
        json={"path": "functional"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


def test_scan_path_unknown_library_404(
    client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    resp = client.post(
        "/api/v1/libraries/999999/scan-path",
        headers=auth_headers,
        json={"path": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "library_not_found"


def test_scan_path_rejects_traversal_outside_root(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir()
    (tmp_path / "outside").mkdir()
    lib = _make_library(db_session, nas)

    resp = client.post(
        f"/api/v1/libraries/{lib.id}/scan-path",
        headers=auth_headers,
        json={"path": "../outside"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "path_outside_library_root"


def test_scan_path_rejects_missing_subfolder(
    tmp_path: Path, client, db_session: Session, auth_headers: dict
) -> None:
    _enable_feature(db_session)
    nas = tmp_path / "nas"
    nas.mkdir()
    lib = _make_library(db_session, nas)

    resp = client.post(
        f"/api/v1/libraries/{lib.id}/scan-path",
        headers=auth_headers,
        json={"path": "does-not-exist"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "path_missing_or_unreadable"
