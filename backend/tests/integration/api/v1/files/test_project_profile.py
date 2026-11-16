"""Only readers of the selected Artifact can read its current derived project facts."""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState
from app.modules.derivatives import producers, records
from app.modules.storage.storage_backend.runtime import get_backend
from tests.fixtures.three_mf_projects import (
    build_3d_builder_component_project,
    build_profile_project,
)


@pytest.fixture
def project(make_model, make_file):
    def build(data=None, **fields):
        if data is None:
            data = build_profile_project(
                {"printer_model": "P1S", "layer_height": "0.2"}
            )
        digest = hashlib.sha256(data).hexdigest()
        key = f"projects/{digest}.3mf"
        get_backend().write_bytes(data, key)
        return make_file(
            make_model("project-profile", **fields),
            filename="project.3mf",
            ftype="3mf",
            path=key,
            size_bytes=len(data),
            sha256=digest,
        )

    return build


class TestFileProjectProfile:
    def test_reads_the_current_derived_profile(self, client, auth_headers, project):
        artifact = project()
        producers.derive_project_profile(artifact.id)
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert result.status_code == 200
        assert result.json() == {
            "state": "ready",
            "failure_reason": None,
            "profile": {
                "name": None,
                "printer": "P1S",
                "materials": [],
                "nozzle_diameters_mm": [],
                "layer_height_mm": 0.2,
                "wall_loops": None,
                "infill_percent": None,
                "plates": [],
            },
        }

    def test_reads_profile_from_the_same_snapshot_as_its_status(
        self, client, auth_headers, project, db_session, monkeypatch
    ):
        artifact = project()
        producers.derive_project_profile(artifact.id)
        read = records.read

        def regenerate_after_status(*args, **kwargs):
            status = read(*args, **kwargs)
            row = db_session.exec(
                select(ArtifactDerivative).where(
                    ArtifactDerivative.file_id == artifact.id,
                    ArtifactDerivative.kind == DerivativeKind.PROJECT_PROFILE,
                )
            ).one()
            db_session.delete(row)
            db_session.commit()
            return status

        monkeypatch.setattr(records, "read", regenerate_after_status)
        response = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["state"] == "ready"
        assert response.json()["profile"]["printer"] == "P1S"

    def test_reports_pending_without_parsing(self, client, auth_headers, project):
        artifact = project(b"not a zip")
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert result.json() == {
            "state": "pending",
            "profile": None,
            "failure_reason": None,
        }

    def test_requires_authentication(self, client, project):
        artifact = project()
        assert (
            client.get(f"/api/v1/files/{artifact.id}/project-profile").status_code
            == 401
        )

    def test_hides_private_artifacts(
        self, client, project, make_collection, make_user, headers_for
    ):
        artifact = project(collection=make_collection("Private profile"))
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile",
            headers=headers_for(make_user("outsider")),
        )
        assert result.status_code == 403

    def test_hides_trashed_models(self, client, auth_headers, project):
        artifact = project(trashed=True)
        assert (
            client.get(
                f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
            ).status_code
            == 404
        )

    def test_refuses_non_3mf(self, client, auth_headers, make_model, make_file):
        artifact = make_file(make_model("mesh-profile"), filename="part.stl")
        assert (
            client.get(
                f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
            ).status_code
            == 404
        )

    def test_skips_unsupported_projects(self, client, auth_headers, project):
        artifact = project(build_3d_builder_component_project())
        producers.derive_project_profile(artifact.id)
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert result.json()["state"] == "skipped"
        assert result.json()["profile"] is None

    def test_reports_malformed_project(self, client, auth_headers, project):
        artifact = project(b"not a zip")
        producers.derive_project_profile(artifact.id)
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert result.json() == {
            "state": "failed",
            "profile": None,
            "failure_reason": "project_profile_malformed",
        }

    def test_reuses_published_output_on_replay(self, db_session, project):
        artifact = project()
        producers.derive_project_profile(artifact.id)
        outcome = producers.derive_project_profile(artifact.id)
        rows = db_session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == artifact.id,
                ArtifactDerivative.kind == DerivativeKind.PROJECT_PROFILE,
            )
        ).all()
        assert outcome.kinds == {}
        assert len(rows) == 1
        assert rows[0].attempts == 1
        assert rows[0].state == DerivativeState.READY

    def test_hides_output_pending_regeneration(
        self, client, auth_headers, db_session, project
    ):
        artifact = project()
        producers.derive_project_profile(artifact.id)
        records.invalidate(db_session, artifact, [DerivativeKind.PROJECT_PROFILE])
        db_session.commit()
        result = client.get(
            f"/api/v1/files/{artifact.id}/project-profile", headers=auth_headers
        )
        assert result.json()["profile"] is None
        assert result.json()["state"] == "pending"

    def test_marks_missing_original_retryable(self, db_session, project, remove_blob):
        artifact = project()
        remove_blob(artifact.path)
        outcome = producers.derive_project_profile(artifact.id)
        row = db_session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == artifact.id)
        ).one()
        assert outcome.kinds == {DerivativeKind.PROJECT_PROFILE: DerivativeState.FAILED}
        assert row.failure_reason == "invalid_source"
        assert row.next_attempt_at is not None
