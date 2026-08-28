"""Mirroring a folder on a NAS into the library, without ever owning its bytes.

An external library is somebody else's directory. PrintStash indexes what is in it and
shows it beside the vault, but the files stay where they are and stay theirs — so the
whole surface is built around not touching them. Deleting the library trashes the index
and leaves the folder alone; a scan that finds an empty or unmounted root aborts rather
than concluding everything was deleted.

The path validation is the security half. A root that overlaps the vault, the staging
directory, the backup directory, the SQLite database file, the secrets key, or **another
external library** is refused, because a scan that walked into any of those would index
PrintStash's own storage as if a user had put it there — and a write-back would then
overwrite it. A scan path is confined to the root for the same reason.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.v1.external_libraries import _to_read
from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    File,
)
from app.db.scopes import live
from app.services import external_library, runtime_config
from app.services.jobs import registry
from tests._env import use_local_storage
from tests.factories import build_collection, build_external_library
from tests.paths import FIXTURES_DIR

FIXTURE_GCODE = FIXTURES_DIR / "sample.gcode"


def _enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


def _drop_gcode(dest_dir: Path, name: str, marker: str | None = None) -> Path:
    """Put a real slicer G-code file into the NAS folder, the way a user would.

    A `marker` appends a unique trailer so the file hashes distinctly, which is what
    makes it a distinct deduplicated Model rather than a second path to the same one.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    base = FIXTURE_GCODE.read_bytes()
    target.write_bytes(
        base + f"\n; unique-marker {marker}\nG1 X0 Y0\n".encode() if marker else base
    )
    return target


def _external_files(session: Session, *, live_only: bool = True) -> list[File]:
    """Every indexed file that lives outside the vault."""
    stmt = select(File).where(File.is_external == True)  # noqa: E712
    if live_only:
        stmt = stmt.where(live(File))
    session.expire_all()
    return list(session.exec(stmt).all())


class TestFeatureGate:
    def test_api_gated_when_feature_disabled(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
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


class TestCreateLibrary:
    def test_the_library_endpoints_accept_only_a_valid_root_path(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
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

    def test_external_library_cannot_overlap_private_storage(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nested = Path(_overlay["data_dir"]) / "nextcloud"
        nested.mkdir(parents=True)

        response = client.post(
            "/api/v1/libraries",
            headers=auth_headers,
            json={"name": "unsafe", "root_path": str(nested)},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "root_path_overlaps_managed_storage"

    def test_create_library_rejects_unreadable_root(
        self,
        tmp_path: Path,
        client,
        db_session: Session,
        auth_headers: dict,
        monkeypatch,
    ) -> None:
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir(parents=True)

        import app.api.v1.external_libraries as ext_api

        monkeypatch.setattr(ext_api.os, "access", lambda *_a, **_k: False)
        resp = client.post(
            "/api/v1/libraries",
            headers=auth_headers,
            json={"name": "x", "root_path": str(nas)},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "root_path_unreadable"

    def test_create_library_schedules_watcher_refresh(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir(parents=True)

        class _FakeWatcher:
            refreshed = False

            def refresh(self) -> None:
                _FakeWatcher.refreshed = True

        client.app.state.library_watcher = _FakeWatcher()
        try:
            resp = client.post(
                "/api/v1/libraries",
                headers=auth_headers,
                json={"name": "watched", "root_path": str(nas)},
            )
            assert resp.status_code == 201
            assert _FakeWatcher.refreshed is True
        finally:
            client.app.state.library_watcher = None


class TestUpdateLibrary:
    def test_updating_the_root_path_recomputes_the_filesystem_kind(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        first_root = tmp_path / "first"
        first_root.mkdir()
        second_root = tmp_path / "second"
        second_root.mkdir()
        lib = build_external_library(db_session, first_root, name="nas")
        # A real collection. `target_collection_id` is a foreign key, so an id that
        # merely happens to be free is refused here exactly as it is on a fresh
        # install. The endpoint does not validate it itself, which is why the
        # literal 42 this used to pass was a 500 on one supported schema and a
        # dangling reference on the other.
        target = build_collection(db_session, name="Target")

        resp = client.patch(
            f"/api/v1/libraries/{lib.id}",
            headers=auth_headers,
            json={
                "root_path": str(second_root),
                "name": "renamed",
                "scan_schedule": "0 0 * * *",
                "watch_mode": "events",
                "collection_mode": "single",
                "target_collection_id": target.id,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["root_path"] == str(second_root)
        assert body["name"] == "renamed"
        assert body["scan_schedule"] == "0 0 * * *"
        assert body["watch_mode"] == "events"
        assert body["collection_mode"] == "single"
        assert body["target_collection_id"] == target.id

    def test_update_library_rejects_invalid_schedule(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir()
        lib = build_external_library(db_session, nas, name="nas")

        resp = client.patch(
            f"/api/v1/libraries/{lib.id}",
            headers=auth_headers,
            json={"scan_schedule": "not a cron"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_cron_schedule"

    def test_update_library_unknown_id_404(
        self, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        resp = client.patch(
            "/api/v1/libraries/999999",
            headers=auth_headers,
            json={"enabled": False},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "library_not_found"


class TestTargetCollection:
    """`target_collection_id` names a real collection, on both write paths.

    It is a foreign key, so an unknown id was a 500 on a fresh installation and a
    dangling reference on one upgraded from an older release — the two supported
    schemas disagree about it (see
    `tests/integration/db/migrations/test_models_versus_chain.py`). Neither is an
    answer to a bad request.
    """

    def test_create_refuses_a_collection_that_does_not_exist(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()

        resp = client.post(
            "/api/v1/libraries",
            headers=auth_headers,
            json={
                "name": "nas",
                "root_path": str(root),
                "collection_mode": "single",
                "target_collection_id": 4242,
            },
        )

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "collection_not_found"

    def test_update_refuses_a_collection_that_does_not_exist(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        lib = build_external_library(db_session, root, name="nas")

        resp = client.patch(
            f"/api/v1/libraries/{lib.id}",
            headers=auth_headers,
            json={"target_collection_id": 4242},
        )

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "collection_not_found"


class TestDeleteLibrary:
    def test_delete_library_unknown_id_404(
        self, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        resp = client.delete("/api/v1/libraries/999999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "library_not_found"

    def test_delete_library_via_api_trashes_index_but_keeps_nas_bytes(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        p1 = _drop_gcode(nas, "a.gcode", marker="a")
        p2 = _drop_gcode(nas, "b.gcode", marker="b")
        lib = build_external_library(db_session, nas, name="nas")
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


class TestScanNow:
    def test_scan_now_queues_job(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        _drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")

        resp = client.post(f"/api/v1/libraries/{lib.id}/scan", headers=auth_headers)
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
        assert job.status_code == 200
        assert job.json()["state"] == "completed", job.json()

    def test_coalesces_onto_the_scan_that_is_already_running(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        _drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        lib.scan_claim_token = "held-by-a-running-scan"
        lib.scan_claim_expires_at = utcnow() + timedelta(minutes=30)
        lib.scan_job_id = "running-job"
        db_session.add(lib)
        db_session.commit()

        resp = client.post(f"/api/v1/libraries/{lib.id}/scan", headers=auth_headers)

        # A scan of a large NAS folder takes minutes, so clicking "scan" again
        # while one is running is the common case, not an edge one. The caller
        # gets the running job's id rather than a second scan of the same tree.
        assert resp.status_code == 202, resp.text
        assert resp.json()["job_id"] == "running-job"

    def test_starts_a_new_scan_once_the_claim_has_expired(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        _drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        lib.scan_claim_token = "left-behind-by-a-killed-process"
        lib.scan_claim_expires_at = utcnow() - timedelta(minutes=1)
        lib.scan_job_id = "abandoned-job"
        db_session.add(lib)
        db_session.commit()

        resp = client.post(f"/api/v1/libraries/{lib.id}/scan", headers=auth_headers)

        # Otherwise a process killed mid-scan would lock the library out of
        # scanning until somebody edited the database by hand.
        assert resp.status_code == 202, resp.text
        assert resp.json()["job_id"] != "abandoned-job"

    def test_scan_now_unknown_library_404(
        self, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        resp = client.post("/api/v1/libraries/999999/scan", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "library_not_found"

    def test_scan_via_api_runs_background_job_to_completion(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        """Full round trip: create a library over HTTP, trigger a scan, and confirm
        the background job completes and the folder is indexed."""
        use_local_storage(tmp_path)
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


class TestScanPath:
    def test_scan_path_queues_job_for_subfolder(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        _drop_gcode(nas / "functional", "bracket.gcode")
        lib = build_external_library(db_session, nas, name="nas")

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
        self, client, db_session: Session, auth_headers: dict
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
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir()
        (tmp_path / "outside").mkdir()
        lib = build_external_library(db_session, nas, name="nas")

        resp = client.post(
            f"/api/v1/libraries/{lib.id}/scan-path",
            headers=auth_headers,
            json={"path": "../outside"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "path_outside_library_root"

    def test_scan_path_rejects_missing_subfolder(
        self, tmp_path: Path, client, db_session: Session, auth_headers: dict
    ) -> None:
        _enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir()
        lib = build_external_library(db_session, nas, name="nas")

        resp = client.post(
            f"/api/v1/libraries/{lib.id}/scan-path",
            headers=auth_headers,
            json={"path": "does-not-exist"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "path_missing_or_unreadable"


class TestOverlapGuards:
    """A root that overlaps something PrintStash owns is refused."""

    def test_refuses_a_root_that_contains_another_external_library(
        self, client: TestClient, db_session: Session, tmp_path: Path, auth_headers
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        inner = tmp_path / "nas" / "inner"
        inner.mkdir(parents=True)
        build_external_library(db_session, inner, name="nas")

        response = client.post(
            "/api/v1/libraries",
            headers=auth_headers,
            json={"name": "outer", "root_path": str(tmp_path / "nas")},
        )

        # A scan of the outer root would walk the inner one and index it twice,
        # and a write-back would then have two owners for the same bytes.
        assert response.status_code == 400, response.text

    def test_refuses_a_root_that_contains_the_database_file(
        self, client: TestClient, db_session: Session, tmp_path: Path, auth_headers
    ) -> None:
        use_local_storage(tmp_path)
        _enable_feature(db_session)
        database = tmp_path / "nas" / "printstash.sqlite"
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"")
        _overlay["db_url"] = f"sqlite:///{database}"

        response = client.post(
            "/api/v1/libraries",
            headers=auth_headers,
            json={"name": "over-db", "root_path": str(tmp_path / "nas")},
        )

        # Indexing the database as if a user had put it there, and then writing
        # back over it, is how a library eats itself.
        assert response.status_code == 400, response.text


class TestToRead:
    """Serialising a library row the scanner may have left in a bad state."""

    def test_reports_no_summary_for_a_row_holding_unparseable_json(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        library = build_external_library(db_session, nas, name="nas")
        library.last_scan_summary = "{not valid json"
        db_session.add(library)
        db_session.commit()

        read = _to_read(library)

        # The summary is a JSON blob written by a scan; a crash mid-write leaves
        # a truncated one. Raising here would 500 the whole libraries listing
        # over a cosmetic field, locking the user out of the page that would let
        # them rescan and fix it.
        assert read.last_scan_summary is None
