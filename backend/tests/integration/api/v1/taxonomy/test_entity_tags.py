"""Direct Collection/Artifact tags and their access-scoped effective counts."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    CollectionRole,
    CollectionTagLink,
    FileTagLink,
    FileType,
    ModelTagLink,
    MultipartModelTagLink,
)
from tests.factories import (
    bearer,
    build_collection,
    build_file,
    build_model,
    build_multipart_model,
    build_tag,
    build_user,
    grant_collection_role,
    tag_collection,
    tag_file,
    tag_model,
    tag_multipart_model,
)


def _assert_replace_collection_tags_is_idempotent(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    collection = build_collection(db_session, "Assemblies")

    first = client.put(
        f"/api/v1/collections/{collection.id}/tags",
        json={"tags": ["Functional", "functional", " PETG "]},
        headers=auth_headers,
    )
    second = client.put(
        f"/api/v1/collections/{collection.id}/tags",
        json={"tags": ["PETG", "Functional"]},
        headers=auth_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["tags"] == ["Functional", "PETG"]
    assert (
        len(
            db_session.exec(
                select(CollectionTagLink).where(
                    CollectionTagLink.collection_id == collection.id
                )
            ).all()
        )
        == 2
    )


def _assert_replace_file_tags_is_idempotent(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = build_model(db_session, "Tagged artifact")
    artifact = build_file(
        db_session, model, filename="choice.3mf", file_type=FileType.THREE_MF
    )

    response = client.put(
        f"/api/v1/models/{model.id}/files/{artifact.id}/tags",
        json={"tags": ["Option A", "option a", "Painted"]},
        headers=auth_headers,
    )
    repeated = client.put(
        f"/api/v1/models/{model.id}/files/{artifact.id}/tags",
        json={"tags": ["Painted", "Option A"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert repeated.status_code == 200
    returned = next(row for row in repeated.json()["files"] if row["id"] == artifact.id)
    assert returned["tags"] == ["Option A", "Painted"]
    assert (
        len(
            db_session.exec(
                select(FileTagLink).where(FileTagLink.file_id == artifact.id)
            ).all()
        )
        == 2
    )


def _assert_entity_tag_edits_require_effective_edit_role(
    client: TestClient, db_session: Session
) -> None:
    collection = build_collection(db_session, "Read only")
    model = build_model(db_session, "Private part", collection=collection)
    artifact = build_file(
        db_session, model, filename="part.stl", file_type=FileType.STL
    )
    viewer = build_user(db_session, "entity-tag-viewer")
    grant_collection_role(db_session, viewer, collection, CollectionRole.VIEW)

    collection_response = client.put(
        f"/api/v1/collections/{collection.id}/tags",
        json={"tags": ["secret"]},
        headers=bearer(viewer),
    )
    file_response = client.put(
        f"/api/v1/models/{model.id}/files/{artifact.id}/tags",
        json={"tags": ["secret"]},
        headers=bearer(viewer),
    )

    assert collection_response.status_code == 403
    assert file_response.status_code == 403
    assert db_session.exec(select(CollectionTagLink)).all() == []
    assert db_session.exec(select(FileTagLink)).all() == []


def _assert_tag_count_is_access_scoped(client: TestClient, db_session: Session) -> None:
    visible_collection = build_collection(db_session, "Visible")
    hidden_collection = build_collection(db_session, "Hidden")
    visible = build_model(db_session, "Visible", collection=visible_collection)
    hidden = build_model(db_session, "Hidden", collection=hidden_collection)
    visible_set = build_multipart_model(
        db_session, "Visible set", collection=visible_collection
    )
    hidden_set = build_multipart_model(
        db_session, "Hidden set", collection=hidden_collection
    )
    visible_file = build_file(db_session, visible, filename="visible.stl")
    tag = build_tag(db_session, "Workshop")
    tag_model(db_session, visible, tag)
    tag_collection(db_session, visible_collection, tag)
    tag_file(db_session, visible_file, tag)
    tag_model(db_session, hidden, tag)
    tag_multipart_model(db_session, visible_set, tag)
    tag_multipart_model(db_session, hidden_set, tag)
    viewer = build_user(db_session, "tag-counter")
    grant_collection_role(db_session, viewer, visible_collection, CollectionRole.VIEW)

    response = client.get("/api/v1/tags", headers=bearer(viewer))

    assert response.status_code == 200
    workshop = next(row for row in response.json() if row["slug"] == "workshop")
    assert workshop["model_count"] == 1
    assert workshop["multipart_model_count"] == 1


def _assert_delete_tag_cleans_every_link_table(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    collection = build_collection(db_session, "Cleanup")
    model = build_model(db_session, "Cleanup model", collection=collection)
    multipart_model = build_multipart_model(
        db_session, "Cleanup set", collection=collection
    )
    artifact = build_file(db_session, model, filename="cleanup.stl")
    tag = build_tag(db_session, "Disposable")
    tag_model(db_session, model, tag)
    tag_collection(db_session, collection, tag)
    tag_file(db_session, artifact, tag)
    tag_multipart_model(db_session, multipart_model, tag)

    response = client.delete(f"/api/v1/tags/{tag.id}", headers=auth_headers)

    assert response.status_code == 204
    assert db_session.exec(select(ModelTagLink)).all() == []
    assert db_session.exec(select(CollectionTagLink)).all() == []
    assert db_session.exec(select(FileTagLink)).all() == []
    assert db_session.exec(select(MultipartModelTagLink)).all() == []


class TestEntityTagEndpoints:
    def test_replace_collection_tags_is_idempotent(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        _assert_replace_collection_tags_is_idempotent(client, db_session, auth_headers)

    def test_replace_file_tags_is_idempotent(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        _assert_replace_file_tags_is_idempotent(client, db_session, auth_headers)

    def test_entity_tag_edits_require_effective_edit_role(
        self, client: TestClient, db_session: Session
    ) -> None:
        _assert_entity_tag_edits_require_effective_edit_role(client, db_session)

    def test_tag_count_is_access_scoped(
        self, client: TestClient, db_session: Session
    ) -> None:
        _assert_tag_count_is_access_scoped(client, db_session)

    def test_delete_tag_cleans_every_link_table(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        _assert_delete_tag_cleans_every_link_table(client, db_session, auth_headers)
