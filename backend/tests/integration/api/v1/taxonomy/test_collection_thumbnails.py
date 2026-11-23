"""Collection thumbnail refresh touches only authorized direct Model representatives."""

import pytest
from sqlmodel import col, select

from app.db.models import (
    ArtifactDerivative,
    CollectionRole,
    DerivativeKind,
    DerivativeState,
    FileType,
)


class TestRegenerateCollectionThumbnails:
    def test_regenerates_only_direct_live_model_thumbnails(
        self,
        client,
        db_session,
        make_collection,
        make_model,
        make_file,
        make_derivative,
        make_user,
        grant_role,
        headers_for,
    ):
        collection = make_collection("Target")
        child = make_collection("Nested", parent=collection)
        model = make_model("Direct", collection=collection)
        representative = make_file(model, file_type=FileType.STL)
        model.thumbnail_file_id = representative.id
        db_session.add(model)
        unrelated = [
            make_file(model, file_type=FileType.STL),
            make_file(make_model("Nested", collection=child), file_type=FileType.STL),
            make_file(make_model("Other"), file_type=FileType.STL),
            make_file(
                make_model("Trash", collection=collection, trashed=True),
                file_type=FileType.STL,
            ),
            make_file(
                make_model("Missing artifact", collection=collection),
                file_type=FileType.STL,
                trashed=True,
            ),
        ]
        for artifact in [representative, *unrelated]:
            make_derivative(artifact, DerivativeKind.THUMBNAIL)
        make_derivative(representative, DerivativeKind.METADATA)
        missing_thumbnail = make_file(
            make_model("Failed thumbnail", collection=collection),
            file_type=FileType.STL,
        )
        make_derivative(
            missing_thumbnail,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            exhausted=True,
        )
        editor = make_user()
        grant_role(editor, collection, CollectionRole.EDIT)
        db_session.commit()

        response = client.post(
            f"/api/v1/collections/{collection.id}/thumbnails/regenerate",
            headers=headers_for(editor),
        )

        assert response.status_code == 202
        assert response.json() == {"queued": 2}
        remaining = db_session.exec(
            select(ArtifactDerivative.file_id).where(
                col(ArtifactDerivative.kind) == DerivativeKind.THUMBNAIL
            )
        ).all()
        assert set(remaining) == {artifact.id for artifact in unrelated}
        assert db_session.exec(
            select(ArtifactDerivative).where(
                col(ArtifactDerivative.file_id) == representative.id,
                col(ArtifactDerivative.kind) == DerivativeKind.METADATA,
            )
        ).one()

    @pytest.mark.parametrize("role, status", [(None, 401), (CollectionRole.VIEW, 403)])
    def test_requires_collection_edit_permission(
        self,
        client,
        make_collection,
        make_user,
        grant_role,
        headers_for,
        role,
        status,
    ):
        collection = make_collection()
        headers = {}
        if role is not None:
            user = make_user()
            grant_role(user, collection, role)
            headers = headers_for(user)
        response = client.post(
            f"/api/v1/collections/{collection.id}/thumbnails/regenerate",
            headers=headers,
        )
        assert response.status_code == status

    def test_empty_collection_queues_nothing(
        self, client, auth_headers, make_collection
    ):
        collection = make_collection()
        response = client.post(
            f"/api/v1/collections/{collection.id}/thumbnails/regenerate",
            headers=auth_headers,
        )
        assert response.status_code == 202
        assert response.json() == {"queued": 0}

    def test_trashed_collection_is_unavailable(
        self, client, auth_headers, make_collection
    ):
        collection = make_collection(trashed=True)
        response = client.post(
            f"/api/v1/collections/{collection.id}/thumbnails/regenerate",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_disabled_thumbnail_producer_keeps_existing_thumbnail(
        self,
        client,
        auth_headers,
        db_session,
        make_collection,
        make_model,
        make_file,
        make_derivative,
        make_system_config,
    ):
        collection = make_collection()
        artifact = make_file(make_model(collection=collection), file_type=FileType.STL)
        derivative = make_derivative(artifact, DerivativeKind.THUMBNAIL)
        make_system_config(derivatives_mesh_enabled=False)
        db_session.commit()
        response = client.post(
            f"/api/v1/collections/{collection.id}/thumbnails/regenerate",
            headers=auth_headers,
        )
        assert response.status_code == 202
        assert response.json() == {"queued": 0}
        assert (
            db_session.exec(
                select(ArtifactDerivative.id).where(
                    col(ArtifactDerivative.id) == derivative.id
                )
            ).one()
            == derivative.id
        )
