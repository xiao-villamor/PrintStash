"""E2E: portable library export/import across two genuinely separate instances.

Instance A is the ``e2e_db`` fixture's on-disk SQLite DB (seeded via the real
ingest pipeline, exactly like ``test_e2e_ingest.py``). Instance B is a second,
independently created file-based SQLite engine + tmp data dir in the same
process -- ``app/services/library_transfer.py``'s ``create_archive``/
``import_archive`` are the real functions, but this drives them through the
real ``/api/v1/models/library-archive`` (export) and ``/library-import``
(import) HTTP endpoints, switching the app's session factory + storage
backend between the two calls (mirroring ``tests/test_backup_restore.py``'s
``backup_env`` fixture, which does the same file-based-DB-swap trick for
backup/restore).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import File, PrintJobState
from app.db.session import SQLiteSessionFactory, override_session_factory
from app.schemas.provenance import CaptureManifestV2
from app.services import provenance, storage_backend
from app.services.setup_token import current_setup_token
from tests.factories import print_job_config
from tests.paths import FIXTURES_DIR

FIXTURE = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"


async def _setup_instance(
    api, tmp_path: Path, *, name: str, username: str
) -> dict[str, str]:
    r = await api.post(
        "/api/v1/setup",
        json={
            "setup_token": current_setup_token(),
            "username": username,
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / name / "files"),
            "thumb_dir": str(tmp_path / name / "thumbs"),
        },
    )
    assert r.status_code == 201, r.text
    from app.services.storage_backend import init_backend

    init_backend()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _switch_to_fresh_instance(tmp_path: Path, name: str) -> None:
    """Point the app's session factory + storage backend at a brand-new DB."""
    root = tmp_path / name
    db_file = root / "db.sqlite"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))
    _overlay["db_url"] = f"sqlite:///{db_file}"
    for key in ("data_dir", "thumb_dir", "staging_dir", "backup_dir"):
        d = root / key
        d.mkdir(parents=True, exist_ok=True)
        _overlay[key] = d
    # get_backend() caches a singleton keyed to whatever data_dir was active
    # when it was first built; force it to rebuild against the new instance.
    storage_backend._backend = None


class TestLibraryTransfer:
    @pytest.mark.asyncio
    async def test_export_from_instance_a_import_into_instance_b_preserves_everything(
        self, api, tmp_path, e2e_db
    ):
        # -- Instance A: seed via the real pipeline -----------------------------
        headers_a = await _setup_instance(
            api, tmp_path, name="instance-a", username="owner-a"
        )

        upload = await api.post(
            "/api/v1/ingest/orca",
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
            data={"model_name": "Transfer Benchy"},
            headers=headers_a,
        )
        assert upload.status_code == 202, upload.text
        job_id = upload.json()["job_id"]
        for _ in range(50):
            status = (
                await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers_a)
            ).json()
            if status["state"] in ("completed", "failed", "duplicate"):
                break
            await asyncio.sleep(0.05)
        assert status["state"] == "completed", status

        models = (await api.get("/api/v1/models", headers=headers_a)).json()
        model = next(m for m in models if m["name"] == "Transfer Benchy")
        model_id = model["id"]

        # Favorite it.
        star = await api.put(f"/api/v1/models/{model_id}/star", headers=headers_a)
        assert star.status_code == 200, star.text

        # A saved view.
        view = await api.post(
            "/api/v1/saved-views",
            headers=headers_a,
            json={"name": "My Benchies", "filters": {"q": "benchy", "favorites": True}},
        )
        assert view.status_code == 201, view.text

        # Print history: seed directly (no printer emulator needed for a transfer
        # test) exactly like test_e2e_notifications.py does for a terminal job.
        detail = (await api.get(f"/api/v1/models/{model_id}", headers=headers_a)).json()
        file_id = detail["files"][0]["id"]
        job = print_job_config(
            model_id=model_id,
            file_id=file_id,
            remote_filename="transfer-benchy.gcode",
            printer_name="Voron 2.4",
            state=PrintJobState.COMPLETED,
            source="external",
            progress=1.0,
            actual_duration_s=3600,
            filament_used_g=15.5,
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        e2e_db.add(job)
        e2e_db.commit()

        artifact = e2e_db.get(File, file_id)
        assert artifact is not None
        capture = CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": "printables",
                    "canonical_url": "https://www.printables.com/model/transfer-benchy",
                    "source_item_id": "transfer-benchy",
                    "source_revision": "r1",
                    "adapter_version": "e2e-adapter-1",
                    "fields": {
                        "title": {"value": "Transfer Benchy", "origin": "confirmed"}
                    },
                },
                "files": [
                    {
                        "id": "transfer-benchy:gcode",
                        "name": artifact.original_filename,
                        "file_type": "gcode",
                        "size": artifact.size_bytes,
                    }
                ],
                "selected_ids": ["transfer-benchy:gcode"],
            }
        )
        provenance.attach_existing_artifact(
            e2e_db,
            artifact,
            provenance.ProvenanceContext(
                manifest=capture,
                source_file_id="transfer-benchy:gcode",
                source_filename=artifact.original_filename,
                blob_sha256=artifact.sha256,
            ),
            imported_overrides={"title": "My Benchy"},
        )
        e2e_db.commit()

        export = await api.get("/api/v1/models/library-archive", headers=headers_a)
        assert export.status_code == 200, export.text
        archive_bytes = export.content
        assert archive_bytes[:2] == b"PK"  # a real zip

        # -- Instance B: a genuinely separate DB + storage tree ------------------
        _switch_to_fresh_instance(tmp_path, "instance-b")
        headers_b = await _setup_instance(
            api, tmp_path, name="instance-b", username="owner-b"
        )

        imported = await api.post(
            "/api/v1/models/library-import",
            headers=headers_b,
            files={
                "file": ("printstash-library-v1.zip", archive_bytes, "application/zip")
            },
        )
        assert imported.status_code == 202, imported.text
        import_status = await api.get(
            f"/api/v1/ingest/jobs/{imported.json()['job_id']}", headers=headers_b
        )
        assert import_status.json()["state"] == "completed", import_status.text
        counts = import_status.json()["result"]
        assert counts["created_models"] == 1
        assert counts["created_files"] == 1
        assert counts["imported_jobs"] == 1

        # Model survives, with its metadata.
        models_b = (await api.get("/api/v1/models", headers=headers_b)).json()
        model_b = next(m for m in models_b if m["name"] == "Transfer Benchy")
        assert model_b["starred"] is True  # favorite followed the archive

        # Saved view survives.
        views_b = (await api.get("/api/v1/saved-views", headers=headers_b)).json()
        assert any(v["name"] == "My Benchies" for v in views_b)
        saved = next(v for v in views_b if v["name"] == "My Benchies")
        assert saved["filters"]["q"] == "benchy"
        assert saved["filters"]["favorites"] is True

        # Print job history survives.
        history_b = (
            await api.get(
                f"/api/v1/models/{model_b['id']}/print-jobs", headers=headers_b
            )
        ).json()
        assert len(history_b) == 1
        assert history_b[0]["printer_name"] == "Voron 2.4"
        assert history_b[0]["filament_used_g"] == 15.5

        provenance_b = (
            await api.get(
                f"/api/v1/models/{model_b['id']}/provenance", headers=headers_b
            )
        ).json()
        assert (
            provenance_b["sources"][0]["captures"][0]["adapter_version"]
            == "e2e-adapter-1"
        )
        assert provenance_b["sources"][0]["fields"][0]["effective_value"] == "My Benchy"

    @pytest.mark.asyncio
    async def test_reimporting_the_same_archive_is_idempotent(
        self, api, tmp_path, e2e_db
    ):
        headers_a = await _setup_instance(
            api, tmp_path, name="idem-a", username="owner-a"
        )
        upload = await api.post(
            "/api/v1/ingest/orca",
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
            data={"model_name": "Idempotent Benchy"},
            headers=headers_a,
        )
        job_id = upload.json()["job_id"]
        for _ in range(50):
            status = (
                await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers_a)
            ).json()
            if status["state"] in ("completed", "failed", "duplicate"):
                break
            await asyncio.sleep(0.05)
        assert status["state"] == "completed", status

        export = await api.get("/api/v1/models/library-archive", headers=headers_a)
        archive_bytes = export.content

        _switch_to_fresh_instance(tmp_path, "idem-b")
        headers_b = await _setup_instance(
            api, tmp_path, name="idem-b", username="owner-b"
        )

        first = await api.post(
            "/api/v1/models/library-import",
            headers=headers_b,
            files={"file": ("archive.zip", archive_bytes, "application/zip")},
        )
        assert first.status_code == 202, first.text
        first_status = await api.get(
            f"/api/v1/ingest/jobs/{first.json()['job_id']}", headers=headers_b
        )
        assert first_status.json()["result"]["created_models"] == 1

        second = await api.post(
            "/api/v1/models/library-import",
            headers=headers_b,
            files={"file": ("archive.zip", archive_bytes, "application/zip")},
        )
        assert second.status_code == 202, second.text
        second_status = await api.get(
            f"/api/v1/ingest/jobs/{second.json()['job_id']}", headers=headers_b
        )
        assert second_status.json()["result"] == {
            "created_models": 0,
            "created_files": 0,
            "skipped_files": 1,
            "imported_jobs": 0,
        }

        models_b = (await api.get("/api/v1/models", headers=headers_b)).json()
        matching = [m for m in models_b if m["name"] == "Idempotent Benchy"]
        assert len(matching) == 1
