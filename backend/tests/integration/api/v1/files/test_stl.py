"""Serving any mesh file as STL, so the browser viewer only ever speaks one format.

Original STL passes through unchanged. Other meshes have durable, on-demand previews:
requests accept preparation, Jobs produce the bytes, and repeated failed requests
retain their reason instead of re-running native parsing inside the API.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    DerivativeKind,
    DerivativeState,
    Job,
    JobKind,
)
from app.modules.derivatives import records
from app.modules.derivatives.kinds import group
from app.modules.derivatives.source import DerivativeSource
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories.three_mf_pilot import load_case
from tests.fixtures.three_mf_projects import (
    build_3d_builder_component_project,
    build_instanced_project,
)
from tests.integration.api.v1._ingest_assertions import drain_work


@pytest.fixture
def project(make_model, make_file):
    payload = build_3d_builder_component_project()
    key = "viewer-project.3mf"
    get_backend().write_bytes(payload, key)
    return make_file(
        make_model("viewer-project"),
        filename=key,
        ftype="3mf",
        path=key,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class TestFileAsStl:
    def test_serves_an_stl_untouched(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("stl-direct")
        key = "already.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        row = make_file(model, filename="already.stl", path=key)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"solid x endsolid"

    def test_answers_a_matching_etag_with_304(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("stl-etag")
        key = "etag.stl"
        get_backend().write_bytes(b"solid y endsolid", key)
        row = make_file(model, filename="etag.stl", path=key)

        response = client.get(
            f"/api/v1/files/{row.id}/stl",
            headers={**auth_headers, "if-none-match": f'"{row.sha256}"'},
        )

        assert response.status_code == 304

    def test_reports_a_missing_blob_as_gone(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("stl-missing"))  # path points nowhere

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    @pytest.mark.parametrize(
        "source_key", [None, "external.stl"], ids=["legacy", "indexed"]
    )
    def test_serves_an_external_stl_without_using_the_active_vault(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
        make_external_library,
        source_key,
        tmp_path: Path,
    ) -> None:
        from app.api.v1 import files as files_api
        from app.modules.storage import artifact_content

        payload = b"solid nas endsolid"
        source = tmp_path / "nas" / "external.stl"
        source.parent.mkdir()
        source.write_bytes(payload)
        library = make_external_library(source.parent, root_identity=None)
        row = make_file(
            make_model("stl-external"),
            filename=source.name,
            path=str(source),
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            is_external=True,
            external_library_id=library.id,
            source_key=source_key,
        )

        def active_vault_must_not_be_used():
            raise AssertionError("external path reached the active vault backend")

        monkeypatch.setattr(
            artifact_content, "get_backend", active_vault_must_not_be_used
        )
        monkeypatch.setattr(files_api, "get_backend", active_vault_must_not_be_used)

        response = client.get(f"/api/v1/files/{row.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == payload

    def test_persists_viewer_demand(self, client, auth_headers, project, db_session):
        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        db_session.refresh(project)
        assert response.status_code == 202, response.text
        assert response.json()["kind"] == "viewer_stl"
        assert project.viewer_requested_at is not None
        assert response.headers["cache-control"] == "private, no-store"

    def test_shares_pending_conversion(self, client, auth_headers, project, db_session):
        first = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        second = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        assert first.status_code == second.status_code == 202
        drain_work()
        jobs = db_session.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert len(jobs) == 1
        assert jobs[0].attempts == 1

    def test_does_not_convert_unopened_meshes(self, project, db_session):
        source = DerivativeSource(group(JobKind.DERIVATIVES_VIEWER_STL))

        pending = source.pending(db_session, now=utcnow(), limit=10)

        assert pending == []
        assert project.viewer_requested_at is None

    def test_serves_placed_geometry(self, client, auth_headers, project):
        accepted = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert accepted.status_code == 202
        assert response.status_code == 200, response.text
        mesh = trimesh.load_mesh(
            io.BytesIO(response.content), file_type="stl", process=False
        )
        np.testing.assert_allclose(
            mesh.bounds, [[110, 220, 330], [112, 223, 334]], atol=1e-5
        )

    def test_retains_resource_refusal(
        self, client, auth_headers, make_model, make_file, db_session, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        payload = build_instanced_project(300)
        get_backend().write_bytes(payload, "refused.3mf")
        file = make_file(
            make_model("refused-viewer"),
            filename="refused.3mf",
            ftype="3mf",
            path="refused.3mf",
            size_bytes=len(payload),
        )
        client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)
        drain_work()
        before = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)
        drain_work()

        after = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)

        assert before.status_code == after.status_code == 422
        assert before.json() == after.json()
        assert after.json()["detail"] == "resource_limit"
        jobs = db_session.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert len(jobs) == 1 and jobs[0].attempts == 1

    def test_retains_required_extension_refusal_without_reprocessing(
        self, client, auth_headers, make_model, make_file, db_session
    ):
        payload = load_case("unknown-required-extension").payload
        key = "unsupported.3mf"
        get_backend().write_bytes(payload, key)
        file = make_file(
            make_model("unsupported-viewer"),
            filename=key,
            ftype="3mf",
            path=key,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        accepted = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)
        assert accepted.status_code == 202, accepted.text
        drain_work()
        first = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)
        drain_work()
        second = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)

        assert first.status_code == second.status_code == 422
        assert first.json() == second.json()
        assert second.json()["detail"] == "unsupported_capability"
        assert second.json()["failure_reason"] == "unsupported_capability"
        assert second.json()["state"] == "failed"
        db_session.refresh(file)
        derivative = records.rows_for(db_session, file)[DerivativeKind.VIEWER_STL]
        assert derivative.storage_key is None
        jobs = db_session.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert len(jobs) == 1 and jobs[0].attempts == 1
        original = client.get(f"/api/v1/files/{file.id}/download", headers=auth_headers)
        assert original.status_code == 200, original.text
        assert original.content == payload

    def test_permits_explicit_retry(
        self, client, auth_headers, project, db_session, make_derivative
    ):
        project.viewer_requested_at = utcnow()
        db_session.add(project)
        db_session.commit()
        make_derivative(
            project,
            DerivativeKind.VIEWER_STL,
            state=DerivativeState.FAILED,
            failure_reason="resource_limit",
            attempts=99,
        )

        accepted = client.post(
            f"/api/v1/files/{project.id}/derivatives/viewer_stl/retry",
            headers=auth_headers,
        )
        drain_work()

        assert accepted.status_code == 202, accepted.text
        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        assert response.status_code == 200, response.text

    def test_reports_cancelled_preview(
        self, client, auth_headers, project, db_session, make_derivative
    ):
        project.viewer_requested_at = utcnow()
        db_session.add(project)
        db_session.commit()
        make_derivative(
            project, DerivativeKind.VIEWER_STL, state=DerivativeState.CANCELLED
        )

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert response.status_code == 422
        assert response.json()["detail"] == "cancelled"

    def test_honors_processing_policy(self, client, auth_headers, project, db_session):
        from app.modules.derivatives.policy import SettingName, update

        update(db_session, {SettingName.MESH: False})

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "derivative_group_disabled"
        db_session.refresh(project)
        assert project.viewer_requested_at is None

    def test_serves_ready_preview_when_disabled(
        self, client, auth_headers, project, db_session
    ):
        from app.modules.derivatives.policy import SettingName, update

        client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()
        update(db_session, {SettingName.MESH: False})

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert len(response.content) > 84

    def test_denies_unauthenticated_preview(self, client, project, db_session):
        response = client.get(f"/api/v1/files/{project.id}/stl")

        assert response.status_code == 401
        db_session.refresh(project)
        assert project.viewer_requested_at is None

    def test_denies_preview_without_collection_access(
        self, client, make_model, make_file, make_collection, make_user, db_session
    ):
        from tests.factories import bearer

        file = make_file(
            make_model("private-viewer", collection=make_collection()),
            filename="private.3mf",
            ftype="3mf",
        )
        outsider = make_user()

        response = client.get(f"/api/v1/files/{file.id}/stl", headers=bearer(outsider))

        assert response.status_code == 403
        db_session.refresh(file)
        assert file.viewer_requested_at is None

    def test_waits_for_artifact_hash(
        self, client, auth_headers, make_model, make_file, db_session
    ):
        from app.db.models import SENTINEL_FILE_HASH

        file = make_file(
            make_model("hash-pending"),
            filename="pending.3mf",
            ftype="3mf",
            sha256=SENTINEL_FILE_HASH,
        )

        response = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)

        assert response.status_code == 409
        db_session.refresh(file)
        assert file.viewer_requested_at is None

    def test_hides_trashed_preview(self, client, auth_headers, make_model, make_file):
        file = make_file(
            make_model("trashed-viewer"),
            filename="hidden.3mf",
            ftype="3mf",
            trashed=True,
        )

        response = client.get(f"/api/v1/files/{file.id}/stl", headers=auth_headers)

        assert response.status_code == 404

    def test_repairs_missing_output(self, client, auth_headers, project, db_session):
        client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()
        db_session.refresh(project)
        derivative = records.rows_for(db_session, project)[DerivativeKind.VIEWER_STL]
        get_backend().direct_path(derivative.storage_key).unlink()

        pending = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()
        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert pending.status_code == 202
        assert response.status_code == 200, response.text

    def test_recovers_demand_after_lost_nudge(self, project, db_session):
        project.viewer_requested_at = utcnow()
        db_session.add(project)
        db_session.commit()
        source = DerivativeSource(group(JobKind.DERIVATIVES_VIEWER_STL))

        pending = source.pending(db_session, now=utcnow(), limit=10)

        assert [item.subject_key for item in pending] == [f"file/{project.id}"]
        assert pending[0].priority.value == "interactive"

    def test_reports_worker_timeout(self, client, auth_headers, project, monkeypatch):
        from app.modules.media.mesh_contracts import ThumbnailFailureReason
        from app.modules.media.mesh_isolation import MeshWorkerError

        def timeout(*args, **kwargs):
            raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)

        monkeypatch.setattr("app.modules.media.stl_isolation.to_stl_bytes", timeout)
        client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert response.status_code == 422
        assert response.json()["detail"] == "timeout"
        assert response.json()["attempts"] == 1

    def test_reports_storage_failure(self, client, auth_headers, project, monkeypatch):
        def unavailable(*args, **kwargs):
            raise OSError("publication unavailable")

        monkeypatch.setattr(
            "app.modules.derivatives.producers.publish_bytes", unavailable
        )
        client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)
        drain_work()

        response = client.get(f"/api/v1/files/{project.id}/stl", headers=auth_headers)

        assert response.status_code == 422
        assert response.json()["detail"] == "storage"

    def test_honors_share_scope(
        self, client, auth_headers, project, make_model, db_session
    ):
        shared = client.post(
            f"/api/v1/models/{make_model('other-shared-model').id}/shares",
            headers=auth_headers,
            json={"allow_download": False},
        )

        response = client.get(
            f"/api/v1/share/{shared.json()['token']}/files/{project.id}/stl"
        )

        assert response.status_code == 404
        db_session.refresh(project)
        assert project.viewer_requested_at is None

    def test_prepares_shared_preview(self, client, auth_headers, project):
        shared = client.post(
            f"/api/v1/models/{project.model_id}/shares",
            headers=auth_headers,
            json={"allow_download": False},
        )
        url = f"/api/v1/share/{shared.json()['token']}/files/{project.id}/stl"
        accepted = client.get(url)
        drain_work()

        response = client.get(url)

        assert accepted.status_code == 202, accepted.text
        assert response.status_code == 200, response.text
        assert len(response.content) > 84
