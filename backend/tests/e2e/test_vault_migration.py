"""A verified Vault move includes online ingestion and never serves partial copies."""

import asyncio
from pathlib import Path

import pytest
from sqlmodel import select

from app.db.models import File, Model
from app.modules.storage import migration_journal, vault_migration
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime.maintenance import end_restore_maintenance


@pytest.fixture
def migration_runtime():
    yield
    for run_id in list(vault_migration._retentions):
        vault_migration._release(run_id)
    end_restore_maintenance()


@pytest.mark.asyncio
async def test_failed_candidate_never_replaces_source_downloads(
    api, e2e_db, superuser_headers, tmp_path, migration_runtime
):
    from app.db.models import VaultMigrationObject

    headers = superuser_headers
    payload = (Path(__file__).parents[1] / "fixtures" / "sample.gcode").read_bytes()
    await ingest(api, headers, "source-authoritative", payload)
    artifact = e2e_db.exec(select(File)).one()
    original_key = artifact.path
    backup = await api.post("/api/v1/backups", headers=headers)
    assert backup.status_code == 202, backup.text
    data, thumbs = tmp_path / "candidate", tmp_path / "candidate-thumbs"
    data.mkdir()
    thumbs.mkdir()
    response = await api.post(
        "/api/v1/storage/migrations/preflight",
        headers=headers,
        json={
            "destination": {
                "provider": "local",
                "data_dir": str(data),
                "thumb_dir": str(thumbs),
            },
            "backup_id": backup.json()["backup_id"],
            "backup_source_ref": backup.json()["source_ref"],
        },
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    base = f"/api/v1/storage/migrations/{plan['id']}"
    assert (
        await api.post(
            base + "/start", headers=headers, json={"plan_digest": plan["plan_digest"]}
        )
    ).status_code == 200
    assert (await api.post(base + "/advance", headers=headers)).status_code == 200
    obj = e2e_db.exec(
        select(VaultMigrationObject).where(
            VaultMigrationObject.run_id == plan["id"],
            VaultMigrationObject.source_key == original_key,
        )
    ).one()
    replacement = Path(obj.destination_key)
    replacement.unlink()
    replacement.write_bytes(b"not owned by this migration")
    failed = await api.post(base + "/cutover", headers=headers)
    assert failed.status_code == 409, failed.text
    assert failed.json()["detail"] == "migration_destination_identity_changed"
    e2e_db.expire_all()
    assert artifact.path == original_key
    read = await api.get(f"/api/v1/files/{artifact.id}/download", headers=headers)
    assert read.status_code == 200
    assert read.content == payload
    assert replacement.read_bytes() == b"not owned by this migration"
    state = await api.get(base, headers=headers)
    assert state.json()["state"] == "paused"


async def ingest(api, headers, name: str, payload: bytes):
    response = await api.post(
        "/api/v1/ingest/orca",
        headers=headers,
        files={"file": (name + ".gcode", payload, "text/x-gcode")},
        data={"model_name": name},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(100):
        job = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        if job.json()["state"] in {"completed", "failed"}:
            assert job.json()["state"] == "completed", job.text
            return
        await asyncio.sleep(0.05)
    raise AssertionError("ingestion did not complete")


@pytest.mark.asyncio
async def test_verified_activation_downloads_every_online_delta_artifact(
    api, e2e_db, superuser_headers, tmp_path, migration_runtime
):
    headers = superuser_headers
    payload = (Path(__file__).parents[1] / "fixtures" / "sample.gcode").read_bytes()
    await ingest(api, headers, "baseline", payload)
    source = get_backend()
    old_keys = [row.path for row in e2e_db.exec(select(File)).all()]
    backup = await api.post("/api/v1/backups", headers=headers)
    assert backup.status_code == 202, backup.text
    data, thumbs = tmp_path / "destination", tmp_path / "destination-thumbs"
    data.mkdir()
    thumbs.mkdir()
    preflight = await api.post(
        "/api/v1/storage/migrations/preflight",
        headers=headers,
        json={
            "destination": {
                "provider": "local",
                "data_dir": str(data),
                "thumb_dir": str(thumbs),
            },
            "backup_id": backup.json()["backup_id"],
            "backup_source_ref": backup.json()["source_ref"],
            "policy": {"retention_days": 0},
        },
    )
    assert preflight.status_code == 200, preflight.text
    plan = preflight.json()
    assert plan["backup_summary"]["archive_sha256"]
    base = f"/api/v1/storage/migrations/{plan['id']}"
    started = await api.post(
        base + "/start", headers=headers, json={"plan_digest": plan["plan_digest"]}
    )
    assert started.status_code == 200, started.text
    copied = await api.post(base + "/advance", headers=headers)
    assert copied.status_code == 200, copied.text
    assert copied.json()["state"] == "ready"
    assert get_backend() is source
    await ingest(api, headers, "delta", payload + b"\n; migration delta\n")
    # Invoke the same durable journal admission used on process startup, then
    # use HTTP recovery to prove the source epoch before explicit resume.
    assert migration_journal.inspect_before_writes()
    blocked = await api.post("/api/v1/backups", headers=headers)
    assert blocked.status_code == 503
    assert blocked.headers["retry-after"] == "5"
    recovered = await api.post(base + "/recover", headers=headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["state"] == "paused"
    assert (await api.post(base + "/resume", headers=headers)).status_code == 200
    switched = await api.post(base + "/cutover", headers=headers)
    assert switched.status_code == 200, switched.text
    assert switched.json()["state"] == "active"
    assert switched.json()["delta_objects"] >= 1
    e2e_db.expire_all()
    for row in e2e_db.exec(select(File)).all():
        model = e2e_db.get(Model, row.model_id)
        expected = (
            payload if model.name == "baseline" else payload + b"\n; migration delta\n"
        )
        response = await api.get(f"/api/v1/files/{row.id}/download", headers=headers)
        assert response.status_code == 200, response.text
        assert response.content == expected
        assert row.path.startswith(str(data))
    assert all(source.exists(key) for key in old_keys)
    refused = await api.post(
        base + "/cleanup",
        headers=headers,
        json={
            "confirmation": plan["id"],
            "source": True,
            "backup_id": backup.json()["backup_id"],
            "backup_source_ref": backup.json()["source_ref"],
        },
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "migration_full_audit_required"
    audited = await api.post(base + "/full-audit", headers=headers)
    assert audited.status_code == 200, audited.text
    assert audited.json()["full_audit"]["critical_count"] == 0
    assert audited.json()["full_audit"]["state"] == "completed"
    refused = await api.post(
        base + "/cleanup",
        headers=headers,
        json={
            "confirmation": plan["id"],
            "source": True,
            "backup_id": backup.json()["backup_id"],
            "backup_source_ref": backup.json()["source_ref"],
        },
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "migration_post_activation_backup_required"
    fresh = await api.post("/api/v1/backups", headers=headers)
    assert fresh.status_code == 202, fresh.text
    cleanup = await api.post(
        base + "/cleanup",
        headers=headers,
        json={
            "confirmation": plan["id"],
            "source": True,
            "backup_id": fresh.json()["backup_id"],
            "backup_source_ref": fresh.json()["source_ref"],
        },
    )
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["state"] == "cleaned"
    assert cleanup.json()["source_retained"] is False
    assert all(not source.exists(key) for key in old_keys)
    from app.db.models import AuditLog

    deletions = e2e_db.exec(
        select(AuditLog).where(AuditLog.action == "vault.migration.object_deleted")
    ).all()
    assert len(deletions) >= len(old_keys)
    assert all(plan["id"] in event.diff_json for event in deletions)
