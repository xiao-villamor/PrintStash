"""E2E: a missing derived thumbnail is found and safely regenerated.

This is the headline maintenance journey through the real ingest and audit
routers: upload a mesh, remove only its derived thumbnail, audit, repair, then
audit again.  It deliberately never mutates the primary Artifact bytes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel import select

from app.db.models import Model
from app.modules.storage.storage_backend.runtime import get_backend, init_backend
from tests.ingestion_work import drain_enrichment, drain_sources

_STL = b"""solid audit_fixture
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid audit_fixture
"""


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    response = await api.post(
        "/api/v1/setup",
        json={
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert response.status_code == 201, response.text
    init_backend()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _await_job(api, headers: dict[str, str], job_id: str) -> dict:
    for _ in range(100):
        await drain_sources()
        response = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] in ("completed", "failed"):
            await drain_enrichment()
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"ingest job did not finish: {job}")


class TestVaultAudit:
    @pytest.mark.asyncio
    async def test_a_quick_audit_repairs_a_missing_thumbnail(
        self, api, tmp_path, e2e_db
    ):
        headers = await _setup_and_login(api, tmp_path)
        upload = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("audit-fixture.stl", _STL, "model/stl")},
            data={"model_name": "Audit fixture"},
            headers=headers,
        )
        assert upload.status_code == 202, upload.text
        assert (await _await_job(api, headers, upload.json()["job_id"]))[
            "state"
        ] == "completed"

        model = e2e_db.exec(select(Model).where(Model.name == "Audit fixture")).one()
        assert model.thumbnail_file_id is not None
        assert model.thumbnail_path is not None
        backend = get_backend()
        assert backend.exists(model.thumbnail_path)
        # Simulate out-of-band loss; unchecked application deletes are disabled.
        Path(model.thumbnail_path).unlink()

        started = await api.post(
            "/api/v1/maintenance/audits", json={"mode": "quick"}, headers=headers
        )
        assert started.status_code == 202, started.text
        audited = await api.get(
            f"/api/v1/maintenance/audits/{started.json()['id']}", headers=headers
        )
        assert audited.status_code == 200, audited.text
        finding = next(
            item
            for item in audited.json()["findings"]
            if item["code"] == "thumbnail_missing"
        )
        assert finding["repair_action"] == "regenerate_thumbnail"

        repaired = await api.post(
            f"/api/v1/maintenance/findings/{finding['id']}/repair", headers=headers
        )
        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["state"] == "resolved"
        assert backend.exists(model.thumbnail_path)

        rerun = await api.post(
            "/api/v1/maintenance/audits", json={"mode": "quick"}, headers=headers
        )
        assert rerun.status_code == 202, rerun.text
        healthy = await api.get(
            f"/api/v1/maintenance/audits/{rerun.json()['id']}", headers=headers
        )
        assert healthy.status_code == 200, healthy.text
        assert "thumbnail_missing" not in {
            item["code"] for item in healthy.json()["findings"]
        }


class TestScheduledVaultAudit:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "repair_action, code",
        [
            ("reparse_metadata", "metadata_missing"),
            ("regenerate_thumbnail", "thumbnail_missing"),
        ],
    )
    async def test_scheduled_audit_records_verified_recovery(
        self, api, tmp_path, e2e_db, fakes, repair_action, code
    ):
        from app.core.time import utcnow
        from app.db.models import Metadata, VaultAuditEvent, VaultAuditPolicy
        from app.runtime.audit_scheduler import run_due_audit

        headers = await _setup_and_login(api, tmp_path)
        from app.modules.notifications import notifications

        assert (
            await api.put(
                "/api/v1/notifications", headers=headers, json={"enabled": True}
            )
        ).status_code == 200
        channel = await api.post(
            "/api/v1/notifications/channels",
            headers=headers,
            json={
                "name": "audit-e2e",
                "target": "webhook",
                "config": {"url": fakes.webhook_url},
                "events": ["storage_regression", "storage_recovery"],
            },
        )
        assert channel.status_code in (200, 201), channel.text
        upload = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("scheduled.stl", _STL, "model/stl")},
            data={"model_name": "Scheduled fixture"},
            headers=headers,
        )
        assert (await _await_job(api, headers, upload.json()["job_id"]))[
            "state"
        ] == "completed"
        if repair_action == "reparse_metadata":
            for metadata in e2e_db.exec(select(Metadata)).all():
                e2e_db.delete(metadata)
            e2e_db.commit()
        else:
            model = e2e_db.exec(select(Model)).one()
            Path(model.thumbnail_path).unlink()
        now = utcnow()
        response = await api.put(
            "/api/v1/maintenance/audit-policies/quick",
            headers=headers,
            json={
                "enabled": True,
                "start_time": now.strftime("%H:%M"),
                "auto_repair": False,
                "notification_cooldown_minutes": 0,
                "notification_threshold": "info",
                "repair_actions": [repair_action],
            },
        )
        assert response.status_code == 200, response.text
        policy = e2e_db.get(VaultAuditPolicy, "quick")
        policy.next_due_at = now
        e2e_db.add(policy)
        e2e_db.commit()
        run_id = await asyncio.to_thread(run_due_audit, now=now)
        assert run_id is not None
        assert await notifications.dispatch_due() == 1
        assert (
            fakes.recorder.for_target("webhook")[0].json["event"]
            == "storage_regression"
        )
        # The unchanged next successful run is quiet, then explicitly enable repair.
        e2e_db.expire_all()
        policy = e2e_db.get(VaultAuditPolicy, "quick")
        policy.next_due_at = utcnow()
        e2e_db.add(policy)
        e2e_db.commit()
        unchanged_id = await asyncio.to_thread(run_due_audit, now=utcnow())
        assert unchanged_id != run_id
        assert await notifications.dispatch_due() == 0
        enabled = await api.put(
            "/api/v1/maintenance/audit-policies/quick",
            headers=headers,
            json={
                "expected_revision": policy.revision,
                "auto_repair": True,
                "repair_actions": [repair_action],
            },
        )
        assert enabled.status_code == 200, enabled.text
        e2e_db.expire_all()
        policy = e2e_db.get(VaultAuditPolicy, "quick")
        policy.next_due_at = utcnow()
        e2e_db.add(policy)
        e2e_db.commit()
        run_id = await asyncio.to_thread(run_due_audit, now=utcnow())
        audited = await api.get(f"/api/v1/maintenance/audits/{run_id}", headers=headers)
        assert audited.json()["state"] == "completed", audited.text
        finding = next(row for row in audited.json()["findings"] if row["code"] == code)
        assert finding["state"] == "resolved"
        events = e2e_db.exec(
            select(VaultAuditEvent).where(VaultAuditEvent.run_id == run_id)
        ).all()
        assert {row.event_type for row in events} == {"storage_recovery"}
        assert e2e_db.exec(select(Metadata)).first() is not None
        assert await notifications.dispatch_due() == 1
        received = fakes.recorder.for_target("webhook")
        assert {row.json["event"] for row in received} == {
            "storage_regression",
            "storage_recovery",
        }
        for row in received:
            data = row.json["data"]
            assert data["maintenance_path"] == "/settings?section=maintenance"
            assert data["duration_s"] >= 0
            assert "scheduled.stl" not in str(row.json)
        history = await api.get("/api/v1/maintenance/audits", headers=headers)
        assert any(
            row["id"] == run_id and row["trigger"] == "scheduled"
            for row in history.json()
        )

    @pytest.mark.asyncio
    async def test_scheduled_full_audit_finds_authoritative_corruption(
        self, api, tmp_path, e2e_db
    ):
        from app.core.time import utcnow
        from app.db.models import File, VaultAuditEvent, VaultAuditPolicy
        from app.runtime.audit_scheduler import run_due_audit

        headers = await _setup_and_login(api, tmp_path)
        upload = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("corrupt.stl", _STL, "model/stl")},
            data={"model_name": "Corrupt fixture"},
            headers=headers,
        )
        assert (await _await_job(api, headers, upload.json()["job_id"]))[
            "state"
        ] == "completed"
        artifact = e2e_db.exec(select(File)).one()
        original = Path(artifact.path).read_bytes()
        Path(artifact.path).write_bytes(b"X" + original[1:])
        now = utcnow()
        response = await api.put(
            "/api/v1/maintenance/audit-policies/full",
            headers=headers,
            json={
                "enabled": True,
                "cadence": "monthly",
                "full_cost_acknowledged": True,
                "start_time": now.strftime("%H:%M"),
            },
        )
        assert response.status_code == 200, response.text
        policy = e2e_db.get(VaultAuditPolicy, "full")
        policy.next_due_at = now
        e2e_db.add(policy)
        e2e_db.commit()
        run_id = await asyncio.to_thread(run_due_audit, now=now)
        audited = await api.get(f"/api/v1/maintenance/audits/{run_id}", headers=headers)
        assert "owned_blob_hash_mismatch" in {
            row["code"] for row in audited.json()["findings"]
        }
        assert any(
            row.event_type == "storage_regression"
            for row in e2e_db.exec(
                select(VaultAuditEvent).where(VaultAuditEvent.run_id == run_id)
            ).all()
        )
        assert Path(artifact.path).read_bytes() == b"X" + original[1:]

    @pytest.mark.asyncio
    async def test_scheduled_full_audit_verifies_backup_with_capacity_callbacks(
        self, api, tmp_path, e2e_db
    ):
        from app.core.time import utcnow
        from app.db.models import CapacityReservation, VaultAuditPolicy
        from app.runtime.audit_scheduler import run_due_audit

        headers = await _setup_and_login(api, tmp_path)
        backup = await api.post("/api/v1/backups", headers=headers)
        assert backup.status_code == 202, backup.text
        now = utcnow()
        configured = await api.put(
            "/api/v1/maintenance/audit-policies/full",
            headers=headers,
            json={
                "enabled": True,
                "full_cost_acknowledged": True,
                "start_time": now.strftime("%H:%M"),
            },
        )
        assert configured.status_code == 200, configured.text
        policy = e2e_db.get(VaultAuditPolicy, "full")
        policy.next_due_at = now
        e2e_db.add(policy)
        e2e_db.commit()

        run_id = await asyncio.to_thread(run_due_audit, now=now)
        audited = await api.get(f"/api/v1/maintenance/audits/{run_id}", headers=headers)
        assert audited.status_code == 200, audited.text
        body = audited.json()
        assert body["state"] == "completed", body
        assert body["bytes_read"] > 0
        assert not [row for row in body["findings"] if row["resource_type"] == "backup"]
        e2e_db.expire_all()
        assert e2e_db.get(VaultAuditPolicy, "full").last_success_at is not None
        assert e2e_db.get(CapacityReservation, f"audit:{run_id}") is None
