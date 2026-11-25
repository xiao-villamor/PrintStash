"""Recreating a Model's views forgets only its mesh Artifacts' published views."""

import pytest
from sqlmodel import col, select

from app.db.models import (
    ArtifactDerivative,
    CollectionRole,
    DerivativeKind,
    FileType,
)


def _kinds(db_session, file):
    return set(
        db_session.exec(
            select(ArtifactDerivative.kind).where(
                col(ArtifactDerivative.file_id) == file.id
            )
        ).all()
    )


@pytest.fixture
def previews_enabled(client, auth_headers):
    saved = client.put(
        "/api/v1/config",
        headers=auth_headers,
        json={"derivatives_previews_enabled": True},
    )
    assert saved.status_code == 200, saved.text


class TestRegenerateModelPreviews:
    @pytest.mark.usefixtures("previews_enabled")
    def test_forgets_the_views_of_each_mesh_artifact_only(
        self, client, auth_headers, db_session, make_model, make_file, make_derivative
    ):
        model = make_model("Tray")
        mesh = make_file(model, file_type=FileType.STL)
        gcode = make_file(model, file_type=FileType.GCODE)
        other = make_file(make_model("Other"), file_type=FileType.STL)
        make_derivative(mesh, DerivativeKind.PREVIEWS)
        make_derivative(mesh, DerivativeKind.THUMBNAIL)
        make_derivative(gcode, DerivativeKind.THUMBNAIL)
        make_derivative(other, DerivativeKind.PREVIEWS)
        db_session.commit()

        response = client.post(
            f"/api/v1/models/{model.id}/previews/regenerate", headers=auth_headers
        )

        assert response.status_code == 202
        assert response.json() == {"queued": 1}
        assert _kinds(db_session, mesh) == {DerivativeKind.THUMBNAIL}
        assert _kinds(db_session, gcode) == {DerivativeKind.THUMBNAIL}
        assert _kinds(db_session, other) == {DerivativeKind.PREVIEWS}

    @pytest.mark.usefixtures("previews_enabled")
    def test_a_model_without_a_mesh_queues_nothing(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("Sliced only")
        make_file(model, file_type=FileType.GCODE)

        response = client.post(
            f"/api/v1/models/{model.id}/previews/regenerate", headers=auth_headers
        )

        assert response.status_code == 202
        assert response.json() == {"queued": 0}

    @pytest.mark.parametrize("role, status", [(None, 401), (CollectionRole.VIEW, 403)])
    @pytest.mark.usefixtures("previews_enabled")
    def test_requires_edit_permission(
        self,
        client,
        make_collection,
        make_model,
        make_user,
        grant_role,
        headers_for,
        role,
        status,
    ):
        collection = make_collection("Locked collection")
        model = make_model("Locked", collection=collection)
        headers = {}
        if role is not None:
            user = make_user()
            grant_role(user, collection, role)
            headers = headers_for(user)

        response = client.post(
            f"/api/v1/models/{model.id}/previews/regenerate", headers=headers
        )

        assert response.status_code == status

    @pytest.mark.usefixtures("previews_enabled")
    def test_a_trashed_model_is_unavailable(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("Gone", trashed=True)
        make_file(model, file_type=FileType.STL)

        response = client.post(
            f"/api/v1/models/{model.id}/previews/regenerate", headers=auth_headers
        )

        assert response.status_code == 404

    def test_refuses_while_the_views_are_switched_off(
        self, client, auth_headers, make_model, make_file
    ):
        model = make_model("Tray")
        make_file(model, file_type=FileType.STL)

        response = client.post(
            f"/api/v1/models/{model.id}/previews/regenerate", headers=auth_headers
        )

        assert response.status_code == 409
