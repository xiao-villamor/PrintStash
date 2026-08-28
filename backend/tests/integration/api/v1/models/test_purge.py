"""Model trash endpoints must fail closed when Artifact ownership is unverified.

These request-level contracts protect rows from being purged when the configured
storage backend cannot prove that PrintStash owns every primary Artifact blob.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, FileType, Model
from app.services.storage_backend import (
    ObjectIdentity,
    StorageCapabilities,
    get_backend,
)
from app.services.storage_ownership import record_creation
from tests.integration.api.v1.documents.test_documents import _headers, _user


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


def _trashed_model_with_owned_blob(session: Session, *, slug: str) -> tuple[Model, str]:
    backend = get_backend()
    model = Model(
        name="Guarded model",
        slug=slug,
        hash="a" * 64,
        deleted_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(model)
    session.flush()
    key = backend.blob_key(slug, 1, "owned.stl")
    receipt = backend.create_bytes(b"owned", key)
    record_creation(session, receipt, object_kind="artifact")
    session.add(
        File(
            model_id=model.id,
            path=key,
            original_filename="owned.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=5,
            sha256="b" * 64,
        )
    )
    session.commit()
    session.refresh(model)
    return model, key


def _make_guarded(monkeypatch) -> None:
    monkeypatch.setattr(
        get_backend(),
        "_capabilities",
        StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        ),
    )


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
    def test_guarded_storage_requires_structured_one_shot_confirmation(
        self, db_session: Session, client: TestClient, monkeypatch
    ) -> None:
        admin = _user(db_session, "guarded-purge-admin", superuser=True)
        model, key = _trashed_model_with_owned_blob(
            db_session, slug="guarded-confirmation"
        )
        _make_guarded(monkeypatch)

        response = client.delete(
            f"/api/v1/models/{model.id}/purge", headers=_headers(admin)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == {
            "code": "storage_risk_confirmation_required",
            "tier": "guarded",
            "operation": "purge_model",
            "required_confirmation": "confirm_storage_risk=true",
        }
        assert get_backend().exists(key)

    def test_guarded_storage_accepts_one_shot_confirmation(
        self, db_session: Session, client: TestClient, monkeypatch
    ) -> None:
        admin = _user(db_session, "confirmed-purge-admin", superuser=True)
        model, key = _trashed_model_with_owned_blob(
            db_session, slug="guarded-confirmed"
        )
        model_id = model.id
        _make_guarded(monkeypatch)

        response = client.delete(
            f"/api/v1/models/{model.id}/purge?confirm_storage_risk=true",
            headers=_headers(admin),
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(Model, model_id) is None
        assert not get_backend().exists(key)

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
