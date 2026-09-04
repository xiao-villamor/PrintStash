"""Soft-deleting a model, restoring it, and permanently purging it.

Trash is what makes deletion recoverable, so the three states have to stay distinct: a
trashed model is invisible to every read path but its rows and its bytes are still there,
a restore puts it back whole, and only a purge actually removes anything.

A purge is the one irreversible operation in the library, so it refuses twice before it
does anything. It refuses a model that is not in the trash — deleting something the user
can still see is never what they asked for — and it refuses outright if the storage
backend cannot **prove** it owns the bytes it is about to remove. That second refusal is a
409 with the whole transaction rolled back: the alternative is deleting a file that
belongs to somebody's external library, which no amount of trash retention can undo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Model
from app.services.storage_backend import (
    ObjectIdentity,
    StorageCapabilities,
    get_backend,
)
from app.services.storage_ownership import UnsafeStorageDeleteError
from tests.factories import build_model, build_stored_file, store_owned_bytes


def _make_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestDeleteModel:
    def test_moves_the_model_to_the_trash(
        self, client: TestClient, db_session: Session, auth_headers, make_model
    ) -> None:
        model = make_model("Doomed bracket")

        response = client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 204, response.text
        db_session.refresh(model)
        assert model.deleted_at is not None

    def test_keeps_the_row_so_it_can_be_restored(
        self, client: TestClient, db_session: Session, auth_headers, make_model
    ) -> None:
        model = make_model("Recoverable bracket")

        client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert db_session.get(Model, model.id) is not None

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.delete("/api/v1/models/999999", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Anonymous delete")

        assert client.delete(f"/api/v1/models/{model.id}").status_code == 401


class TestRestoreModel:
    def test_brings_a_trashed_model_back(
        self, client: TestClient, db_session: Session, auth_headers, make_model
    ) -> None:
        model = make_model("Restorable", deleted_at=utcnow())

        response = client.post(
            f"/api/v1/models/{model.id}/restore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        db_session.refresh(model)
        assert model.deleted_at is None

    def test_puts_it_back_in_the_library_listing(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Restored to library", deleted_at=utcnow())

        client.post(f"/api/v1/models/{model.id}/restore", headers=auth_headers)

        listed = client.get("/api/v1/models", headers=auth_headers).json()
        assert model.id in {item["id"] for item in listed}

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.post("/api/v1/models/999999/restore", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Anonymous restore", deleted_at=utcnow())

        assert client.post(f"/api/v1/models/{model.id}/restore").status_code == 401


class TestPurgeModel:
    def test_guarded_storage_requires_structured_one_shot_confirmation(
        self,
        db_session: Session,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model = build_model(db_session, "Guarded model", trashed=True)
        artifact = build_stored_file(db_session, get_backend(), model, data=b"owned")
        key = artifact.path
        _make_guarded(monkeypatch)

        response = client.delete(
            f"/api/v1/models/{model.id}/purge", headers=auth_headers
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
        self,
        db_session: Session,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model = build_model(db_session, "Guarded model", trashed=True)
        artifact = build_stored_file(db_session, get_backend(), model, data=b"owned")
        key = artifact.path
        model_id = model.id
        _make_guarded(monkeypatch)

        response = client.delete(
            f"/api/v1/models/{model.id}/purge?confirm_storage_risk=true",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(Model, model_id) is None
        body = response.json()
        assert body["storage_blocked"] == 1
        assert body["storage_cleanup_status"] == "blocked"
        assert get_backend().exists(key)

    def test_permanently_removes_a_trashed_model(
        self, client: TestClient, db_session: Session, auth_headers, make_model
    ) -> None:
        model = make_model("Purgeable", deleted_at=utcnow())
        model_id = model.id
        db_session.expunge(model)

        response = client.delete(
            f"/api/v1/models/{model_id}/purge", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["purged_model_ids"] == [model_id]
        db_session.expire_all()
        assert db_session.get(Model, model_id) is None

    def test_refuses_a_model_that_is_not_in_the_trash(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Still live")

        response = client.delete(
            f"/api/v1/models/{model.id}/purge", headers=auth_headers
        )

        # Deleting something the user can still see is never what they asked for.
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "model_not_in_trash"

    def test_refuses_when_storage_cannot_prove_it_owns_the_bytes(
        self,
        client: TestClient,
        auth_headers,
        make_model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        model = make_model("Unowned bytes", deleted_at=utcnow())

        def unproven(*_args: object, **_kwargs: object):
            raise UnsafeStorageDeleteError("storage_ownership_unverified")

        monkeypatch.setattr(models_api, "hard_delete_model", unproven)

        response = client.delete(
            f"/api/v1/models/{model.id}/purge", headers=auth_headers
        )

        # The bytes could belong to somebody's external library.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"

    def test_keeps_the_model_when_ownership_cannot_be_proven(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        model = make_model("Unowned but kept", deleted_at=utcnow())

        def unproven(session, _model, **_kwargs: object):
            session.delete(_model)
            raise UnsafeStorageDeleteError("storage_ownership_unverified")

        monkeypatch.setattr(models_api, "hard_delete_model", unproven)

        client.delete(f"/api/v1/models/{model.id}/purge", headers=auth_headers)

        # A half-applied purge is worse than no purge: the row would be gone and
        # the bytes would be orphaned.
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.delete("/api/v1/models/999999/purge", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Anonymous purge", deleted_at=utcnow())

        assert client.delete(f"/api/v1/models/{model.id}/purge").status_code == 401


class TestPurgeExpiredTrash:
    def test_purges_a_model_past_its_retention(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import timedelta

        from app.core.config import _overlay

        model = make_model("Long expired", deleted_at=utcnow() - timedelta(days=90))
        monkeypatch.setitem(_overlay, "trash_retention_days", 30)

        response = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert model.id in response.json()["purged_model_ids"]

    def test_keeps_a_model_still_inside_its_retention(
        self,
        client: TestClient,
        auth_headers,
        make_model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import _overlay

        model = make_model("Recently trashed", deleted_at=utcnow())
        monkeypatch.setitem(_overlay, "trash_retention_days", 30)

        response = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

        assert model.id not in response.json()["purged_model_ids"]

    def test_reports_what_the_storage_backend_did(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

        body = response.json()
        assert {"storage_completed", "storage_pending", "storage_blocked"} <= set(body)

    def test_refuses_when_storage_cannot_prove_it_owns_the_bytes(
        self, client: TestClient, auth_headers, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import models as models_api

        def unproven(*_args: object, **_kwargs: object):
            raise UnsafeStorageDeleteError("storage_ownership_unverified")

        monkeypatch.setattr(models_api, "hard_delete_expired_models", unproven)

        response = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"

    def test_rejects_a_non_superuser(self, client: TestClient, user_headers) -> None:
        response = client.delete(
            "/api/v1/models/trash/expired", headers=user_headers("trash-ordinary")
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.delete("/api/v1/models/trash/expired").status_code == 401

    def test_removes_the_expired_models_bytes_from_storage(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import timedelta

        from app.core.config import _overlay
        from app.db.models import FileType
        from app.services.storage_backend import get_backend
        from tests._env import use_local_storage
        from tests.factories import build_file

        use_local_storage(tmp_path)
        monkeypatch.setitem(_overlay, "trash_retention_days", 30)
        expired = make_model("Long expired", deleted_at=utcnow() - timedelta(days=90))
        fresh = make_model("Recently trashed", deleted_at=utcnow())
        paths = {}
        for index, (model, name) in enumerate(((expired, "old"), (fresh, "new"))):
            path = str(tmp_path / "files" / f"{name}-cube.stl")
            build_file(
                db_session,
                model,
                path=path,
                filename=f"{name}-cube.stl",
                file_type=FileType.STL,
                size_bytes=3,
                sha256=chr(ord("a") + index) * 64,
            )
            store_owned_bytes(
                db_session,
                get_backend(),
                path,
                name.encode(),
                object_kind="artifact",
            )
            paths[name] = Path(path)
        db_session.commit()

        response = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

        # The row going is not the point — the bytes are. A purge that deleted
        # the row and left the blob is a storage leak nothing will ever collect,
        # and one that deleted the wrong blob takes a model the user can still
        # see in their trash.
        assert response.status_code == 200, response.text
        assert not paths["old"].exists()
        assert paths["new"].exists()
