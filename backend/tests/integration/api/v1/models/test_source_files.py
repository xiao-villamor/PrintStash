"""A source Artifact can leave a Model without moving its siblings or Revisions."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import CollectionRole, File, FileType, Model, MultipartModelChoice
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import build_print_job, build_stored_file


def _source(make_file, model: Model, name: str, **overrides) -> File:
    return make_file(model, file_type=FileType.STL, filename=name, **overrides)


class TestSourceFileLifecycle:
    def test_trashing_source_preserves_model_content(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        local_storage,
    ) -> None:
        model = make_model("Parts")
        removed = build_stored_file(
            db_session, get_backend(), model, filename="left.stl", data=b"left"
        )
        sibling = build_stored_file(
            db_session, get_backend(), model, filename="right.stl", data=b"right"
        )
        revision = build_stored_file(
            db_session,
            get_backend(),
            model,
            filename="print.gcode",
            file_type=FileType.GCODE,
            data=b"G28\n",
            recommended=True,
        )
        job = build_print_job(db_session, revision)
        model.thumbnail_file_id = removed.id
        model.thumbnail_path = "/data/thumbnails/left.webp"
        db_session.add(model)
        db_session.commit()

        response = client.delete(
            f"/api/v1/models/{model.id}/files/{removed.id}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert {row["id"] for row in response.json()["files"]} == {
            sibling.id,
            revision.id,
        }
        assert response.json()["thumbnail_url"] is None
        db_session.refresh(removed)
        db_session.refresh(revision)
        assert removed.deleted_at is not None
        assert revision.is_recommended is True
        assert db_session.get(Model, model.id).deleted_at is None
        assert db_session.get(type(job), job.id) is not None
        assert get_backend().exists(removed.path)
        assert get_backend().exists(sibling.path)

    def test_trashing_last_source_keeps_model(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("Source-less")
        source = _source(make_file, model, "only.stl")

        deleted = client.delete(
            f"/api/v1/models/{model.id}/files/{source.id}", headers=auth_headers
        )

        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["files"] == []
        assert deleted.json()["trashed_source_files"] == [
            {"id": source.id, "original_filename": "only.stl"}
        ]
        assert db_session.get(Model, model.id).deleted_at is None

    def test_restores_trashed_source(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("Restore source")
        source = _source(make_file, model, "only.stl")
        client.delete(
            f"/api/v1/models/{model.id}/files/{source.id}", headers=auth_headers
        )

        restored = client.post(
            f"/api/v1/models/{model.id}/files/{source.id}/restore",
            headers=auth_headers,
        )

        assert restored.status_code == 200, restored.text
        assert [row["id"] for row in restored.json()["files"]] == [source.id]
        assert restored.json()["trashed_source_files"] == []

    def test_linked_source_original_cannot_be_removed(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("Linked")
        linked = _source(make_file, model, "remote.stl", is_external=True)

        response = client.delete(
            f"/api/v1/models/{model.id}/files/{linked.id}", headers=auth_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "linked_source_protected"
        db_session.refresh(linked)
        assert linked.deleted_at is None

    def test_revision_must_use_its_existing_lifecycle(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("Revisions")
        revision = make_file(model)

        response = client.delete(
            f"/api/v1/models/{model.id}/files/{revision.id}", headers=auth_headers
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "source_file_required"

    def test_viewer_cannot_trash_a_source(
        self,
        client: TestClient,
        db_session: Session,
        make_model,
        make_file,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        collection = make_collection("Private")
        model = make_model("Private part", collection_id=collection.id)
        source = _source(make_file, model, "private.stl")
        viewer = make_user("source-viewer")
        grant_role(viewer, collection, CollectionRole.VIEW)

        response = client.delete(
            f"/api/v1/models/{model.id}/files/{source.id}", headers=headers_for(viewer)
        )

        assert response.status_code == 403, response.text
        db_session.refresh(source)
        assert source.deleted_at is None

    def test_restore_refuses_a_file_already_claimed_for_purge(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("Purge claimed")
        source = _source(
            make_file,
            model,
            "claimed.stl",
            deleted_at=utcnow(),
            purge_token="claimed-by-cleanup",
        )

        response = client.post(
            f"/api/v1/models/{model.id}/files/{source.id}/restore",
            headers=auth_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_cleanup_blocked"
        db_session.refresh(source)
        assert source.deleted_at is not None

    def test_pinned_multipart_choice_survives_source_trash(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("Pinned part")
        source = _source(make_file, model, "pinned.stl")
        created = client.post(
            "/api/v1/multipart-models", headers=auth_headers, json={"name": "Assembly"}
        ).json()
        aggregate_id = created["id"]
        saved = client.put(
            f"/api/v1/multipart-models/{aggregate_id}",
            headers=auth_headers,
            json={
                "name": "Assembly",
                "parts": [{"name": "Body", "choices": [{"model_id": model.id}]}],
            },
        ).json()
        choice_id = saved["parts"][0]["models"][0]["choice_id"]
        choice = db_session.get(MultipartModelChoice, choice_id)
        choice.source_file_id = source.id
        db_session.add(choice)
        db_session.commit()

        client.delete(
            f"/api/v1/models/{model.id}/files/{source.id}", headers=auth_headers
        )

        trashed = client.get(
            f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
        )
        assert trashed.status_code == 200, trashed.text
        assert trashed.json()["parts"][0]["models"][0]["available"] is False
        assert db_session.get(MultipartModelChoice, choice_id).model_id == model.id

        client.post(
            f"/api/v1/models/{model.id}/files/{source.id}/restore",
            headers=auth_headers,
        )
        restored = client.get(
            f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
        )
        assert restored.json()["parts"][0]["models"][0]["available"] is True
