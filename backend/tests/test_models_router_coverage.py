"""Model trash endpoints must fail closed when Artifact ownership is unverified.

These request-level contracts protect rows from being purged when the configured
storage backend cannot prove that PrintStash owns every primary Artifact blob.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, FileType, Model
from app.services.storage_backend import get_backend
from tests.test_documents import _headers, _user


def _trashed_model_with_missing_blob(
    session: Session, *, name: str, slug: str
) -> Model:
    model = Model(
        name=name,
        slug=slug,
        hash=slug[0] * 64,
        deleted_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(model)
    session.flush()
    session.add(
        File(
            model_id=model.id,
            path=get_backend().blob_key(slug, 1, "missing.stl"),
            original_filename="missing.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=12,
            sha256="f" * 64,
        )
    )
    session.commit()
    session.refresh(model)
    return model


class TestPurgeExpiredTrash:
    def test_unverified_storage_returns_conflict_without_deleting_model(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "expired-trash-admin", superuser=True)
        model = _trashed_model_with_missing_blob(
            db_session, name="Expired", slug="expired-unverified"
        )

        response = client.delete(
            "/api/v1/models/trash/expired", headers=_headers(admin)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None


class TestPurgeModel:
    def test_unverified_storage_returns_conflict_without_deleting_model(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "model-purge-admin", superuser=True)
        model = _trashed_model_with_missing_blob(
            db_session, name="Trashed", slug="trashed-unverified"
        )

        response = client.delete(
            f"/api/v1/models/{model.id}/purge", headers=_headers(admin)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None
