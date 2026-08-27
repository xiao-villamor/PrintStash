"""Raw file download behavior against the real local storage backend."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, FileType, Model, User
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import get_backend


def _artifact(session: Session, *, key: str, filename: str) -> File:
    model = Model(name="Download", slug=f"download-{filename}", hash="e" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    artifact = File(
        model_id=model.id,
        path=key,
        original_filename=filename,
        file_type=FileType.GCODE,
        version=1,
        size_bytes=7,
        sha256="f" * 64,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


class TestDownloadFile:
    def test_download_file_streams_authorized_blob_with_disposition(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        key = "downloads/bracket.gcode"
        get_backend().write_bytes(b"G28\nM84", key)
        artifact = _artifact(db_session, key=key, filename="bracket.gcode")

        # Act
        response = client.get(
            f"/api/v1/files/{artifact.id}/download", headers=auth_headers
        )

        # Assert
        assert response.status_code == 200
        assert response.content == b"G28\nM84"
        assert response.headers["content-disposition"].endswith(
            'filename="bracket.gcode"'
        )

    def test_download_file_returns_not_found_for_a_missing_record(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.get("/api/v1/files/999999/download", headers=auth_headers)

        assert response.status_code == 404

    def test_download_file_reports_a_missing_blob_as_gone(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        artifact = _artifact(
            db_session, key="downloads/missing.gcode", filename="missing.gcode"
        )

        response = client.get(
            f"/api/v1/files/{artifact.id}/download", headers=auth_headers
        )

        assert response.status_code == 410
        assert response.json()["detail"] == "file_blob_missing"


class TestRebuildMissingThumbnails:
    def test_rebuild_missing_thumbnails_creates_background_job(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.post("/api/v1/files/thumbnails/rebuild", headers=auth_headers)

        # Assert
        assert response.status_code == 202
        assert response.json()["job_id"]
        assert response.json()["message"] == "thumbnail rebuild queued"

    def test_rebuild_missing_thumbnails_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        user = User(
            username="thumbnail-reader",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=False,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        token = create_access_token(user.id, user.username, scope="write")
        regular_headers = {"Authorization": f"Bearer {token}"}

        # Act
        anonymous = client.post("/api/v1/files/thumbnails/rebuild")
        regular = client.post(
            "/api/v1/files/thumbnails/rebuild", headers=regular_headers
        )

        # Assert
        assert anonymous.status_code == 401
        assert regular.status_code == 403
