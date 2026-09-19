"""Optional previews are accepted as durable work with ordinary edit permissions."""

from sqlmodel import select

from app.db.models import ArtifactAnalysisGeneration, ThumbnailGeneration
from tests.factories import bearer, build_file, build_model


class TestEnrichmentContract:
    def test_a_collection_reader_cannot_schedule_computation(
        self, client, db_session, make_user, make_collection, grant_role
    ):
        user = make_user()
        collection = make_collection()
        grant_role(user, collection)
        file = build_file(db_session, build_model(db_session, collection=collection))

        response = client.post(
            f"/api/v1/files/{file.id}/enrichment", headers=bearer(user), json={}
        )

        assert response.status_code == 403, response.text
        assert db_session.exec(select(ArtifactAnalysisGeneration)).all() == []
        assert db_session.exec(select(ThumbnailGeneration)).all() == []

    def test_a_read_only_token_cannot_schedule_computation(
        self, client, db_session, make_user, make_model, make_file
    ):
        user = make_user(superuser=True)
        file = build_file(db_session, build_model(db_session))

        response = client.post(
            f"/api/v1/files/{file.id}/enrichment",
            headers=bearer(user, scope="read"),
            json={},
        )

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"
        assert db_session.exec(select(ArtifactAnalysisGeneration)).all() == []
        assert db_session.exec(select(ThumbnailGeneration)).all() == []

    def test_requests_a_preview_without_rendering(
        self,
        client,
        auth_headers,
        db_session,
        make_model,
        make_file,
        make_thumbnail_generation,
    ):
        file = build_file(db_session, build_model(db_session))
        generation = make_thumbnail_generation(file, processing_policy="on_demand")

        response = client.post(
            f"/api/v1/files/{file.id}/enrichment",
            headers=auth_headers,
            json={"metadata": False, "thumbnail": True},
        )

        assert response.status_code == 202
        assert response.json()["thumbnail"] == "pending"
        db_session.refresh(generation)
        assert generation.processing_policy == "background"
        assert generation.storage_key is None
        assert db_session.exec(select(ArtifactAnalysisGeneration)).first() is None

    def test_requires_authentication(self, client, db_session):
        file = build_file(db_session, build_model(db_session))
        response = client.post(f"/api/v1/files/{file.id}/enrichment", json={})
        assert response.status_code == 401

    def test_refuses_trashed_sources(self, client, auth_headers, db_session):
        file = build_file(db_session, build_model(db_session, trashed=True))
        response = client.post(
            f"/api/v1/files/{file.id}/enrichment", headers=auth_headers, json={}
        )
        assert response.status_code == 404

    def test_coalesces_repeated_requests(
        self, client, auth_headers, db_session, make_model, make_file
    ):
        file = build_file(db_session, build_model(db_session))
        for _ in range(2):
            assert (
                client.post(
                    f"/api/v1/files/{file.id}/enrichment", headers=auth_headers, json={}
                ).status_code
                == 202
            )
        assert len(db_session.exec(select(ArtifactAnalysisGeneration)).all()) == 1
        assert len(db_session.exec(select(ThumbnailGeneration)).all()) == 1
