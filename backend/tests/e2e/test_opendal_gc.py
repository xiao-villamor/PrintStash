"""An administrator-declared OpenDAL S3 replica witnesses the real GC API flow.

The Docker network is a simulation of separately administered storage; the
application resolves its real endpoint and requires the same explicit domain
declaration used for custom production S3. No identity or backup verifier is stubbed.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config as BotoConfig
from sqlmodel import select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import File, GcRun, Model
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_private_endpoint
from tests.paths import FIXTURES_DIR


@pytest.fixture
def independent_s3_bucket():
    endpoint = s3_private_endpoint()
    bucket = f"gc-witness-{uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=bucket)
    try:
        yield endpoint, bucket, client
    finally:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)


class TestOpenDalGc:
    @pytest.mark.s3
    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_independent_s3_replica_authorizes_only_quarantined_gc_candidates(
        self, api, superuser_headers, e2e_db, independent_s3_bucket
    ) -> None:
        endpoint, bucket, remote = independent_s3_bucket
        headers = superuser_headers
        configured = await api.put(
            "/api/v1/config",
            headers=headers,
            json={"manual_local_backup_enabled": False, "trash_retention_days": 30},
        )
        assert configured.status_code == 200, configured.text
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=headers,
            json={
                "name": "GC remote backup",
                "kind": "s3",
                "purpose": "backup",
                "configuration": {
                    "provider": "s3_self_hosted",
                    "bucket": bucket,
                    "endpoint_url": endpoint,
                    "region": "us-east-1",
                    "root": "offsite",
                    "addressing_style": "path",
                },
                "secrets": {"access_key": S3_ACCESS_KEY, "secret_key": S3_SECRET_KEY},
            },
        )
        assert connection.status_code == 201, connection.text
        payload = (FIXTURES_DIR / "sample.gcode").read_bytes()
        uploaded = await api.post(
            "/api/v1/ingest/orca",
            headers=headers,
            files={"file": ("sample.gcode", payload, "text/plain")},
            data={"model_name": "GC candidate"},
        )
        assert uploaded.status_code == 202, uploaded.text
        for _ in range(50):
            job = (
                await api.get(
                    f"/api/v1/ingest/jobs/{uploaded.json()['job_id']}", headers=headers
                )
            ).json()
            if job["state"] in {"completed", "failed", "duplicate"}:
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "completed", job
        e2e_db.expire_all()
        candidate = e2e_db.exec(select(Model).where(Model.name == "GC candidate")).one()
        artifact = e2e_db.exec(select(File).where(File.model_id == candidate.id)).one()
        candidate_id, artifact_id = candidate.id, artifact.id
        untouched = Path(settings.data_dir) / "external-original.stl"
        untouched.write_bytes(b"not owned by PrintStash")
        created = await api.post("/api/v1/backups", headers=headers)
        assert created.status_code == 202, created.text
        archive = created.json()
        assert archive["location"] == "opendal:s3"
        assert not list(Path(settings.backup_dir).glob("*.tar.gz"))
        assert (
            await api.delete(f"/api/v1/models/{candidate_id}", headers=headers)
        ).status_code == 204
        e2e_db.expire_all()
        candidate = e2e_db.get(Model, candidate_id)
        candidate.deleted_at = utcnow() - timedelta(days=31)
        e2e_db.add(candidate)
        e2e_db.commit()
        preview = await api.post("/api/v1/admin/gc", headers=headers)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan["resource_count"] == 1
        refused = await api.post(
            f"/api/v1/admin/gc/{plan['id']}/approve",
            headers=headers,
            json={"digest": plan["digest"]},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"] == "gc_backup_required"
        targets = (await api.get("/api/v1/storage/targets", headers=headers)).json()
        target = next(item for item in targets if item["role"] == "backup")
        declared = await api.put(
            f"/api/v1/storage/targets/{target['target_ref']}/failure-domain",
            headers=headers,
            json={"failure_domain": "offsite-gc-test"},
        )
        assert declared.status_code == 200, declared.text
        approved = await api.post(
            f"/api/v1/admin/gc/{plan['id']}/approve",
            headers=headers,
            json={"digest": plan["digest"]},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "quarantined"
        assert approved.json()["backup_source_ref"] == archive["source_ref"]
        assert approved.json()["backup_archive_sha256"] == archive["archive_sha256"]
        premature = await api.post(
            f"/api/v1/admin/gc/{plan['id']}/finalize", headers=headers
        )
        assert premature.status_code == 409
        assert premature.json()["detail"] == "gc_quarantine_active"
        e2e_db.expire_all()
        assert e2e_db.get(File, artifact_id) is not None
        # Advance the persisted quarantine deadline without changing the
        # approved digest, identity evidence or any deletion implementation.
        run = e2e_db.get(GcRun, plan["id"])
        run.quarantine_until = utcnow() - timedelta(seconds=1)
        e2e_db.add(run)
        e2e_db.commit()
        finalized = await api.post(
            f"/api/v1/admin/gc/{plan['id']}/finalize", headers=headers
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["state"] == "completed"
        e2e_db.expire_all()
        assert e2e_db.get(Model, candidate_id) is None
        assert e2e_db.get(File, artifact_id) is None
        assert untouched.read_bytes() == b"not owned by PrintStash"
        assert remote.list_objects_v2(Bucket=bucket)["KeyCount"] == 1
        verified = await api.post(
            f"/api/v1/backups/{archive['backup_id']}/verify",
            headers=headers,
            params={"source_ref": archive["source_ref"]},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["valid"] is True
