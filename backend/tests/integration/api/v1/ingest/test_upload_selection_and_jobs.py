"""Ingestion upload, selection, job-status, and authentication HTTP behaviours.

These cases defend authentication, request bounds, reconnect cache policy, and
the ownership of in-process review tokens that are observable at the HTTP seam.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.v1 import ingest as ingest_api
from app.db.models import BackgroundJob, ExternalLibrary, File, User
from app.services import import_resolvers, runtime_config
from app.services.auth import create_access_token, hash_password
from app.services.jobs import registry

from ._ingest_api_shared import _completed_job, _configure_storage, _cube_stl


def _headers(session: Session, username: str) -> tuple[dict[str, str], User]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}, user


@pytest.fixture(autouse=True)
def _isolate_review_registries() -> Iterator[None]:
    yield
    ingest_api.pending_model_files._items.clear()
    ingest_api.pending_collections._items.clear()


class TestIngestUploads:
    def test_routes_a_model_upload_to_an_eligible_external_library(
        self,
        tmp_path,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        _configure_storage(tmp_path)
        library_root = tmp_path / "external-library"
        library_root.mkdir()
        runtime_config.set_external_libraries_enabled(db_session, True)
        library = ExternalLibrary(name="NAS", root_path=str(library_root), enabled=True)
        db_session.add(library)
        db_session.commit()
        db_session.refresh(library)

        job = _completed_job(
            client,
            client.post(
                "/api/v1/ingest/model",
                headers=auth_headers,
                files={"file": ("cube.stl", _cube_stl(), "application/sla")},
                data={"target_library_id": str(library.id)},
            ),
        )

        artifact = db_session.get(File, job["file_id"])
        assert artifact is not None
        assert artifact.is_external is True
        assert artifact.external_library_id == library.id

    def test_rejects_an_invalid_orca_source_hash_without_creating_a_job(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        response = client.post(
            "/api/v1/ingest/orca",
            headers=auth_headers,
            files={"file": ("cube.gcode", b"G90\n", "text/plain")},
            data={"source_hash": "not-a-sha256"},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "source_hash_invalid"
        assert db_session.exec(select(BackgroundJob)).all() == []


class TestSelectModelFiles:
    def test_hides_another_users_model_files_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "files-token-requester")
        assert owner.id is not None
        token = ingest_api.pending_model_files.add(
            ingest_api._PendingModelFiles(
                page_url="https://www.printables.com/model/1",
                page_title="Private model",
                owner_user_id=owner.id + 1,
                files=[
                    import_resolvers.ModelFile(
                        file_id="private-file",
                        name="private.stl",
                        file_type="stl",
                        size=10,
                    )
                ],
            )
        )
        db_session.rollback()

        response = client.post(
            f"/api/v1/ingest/url/files/{token}/select",
            headers=headers,
            json={"file_ids": ["private-file"]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "files_not_found"
        assert ingest_api.pending_model_files.get(token) is not None


class TestSelectCollectionMembers:
    def test_hides_another_users_collection_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "collection-token-requester")
        assert owner.id is not None
        token = ingest_api.pending_collections.add(
            ingest_api._PendingCollection(
                title="Private collection",
                target_collection="Private collection",
                owner_user_id=owner.id + 1,
                members=[
                    import_resolvers.CollectionMember(
                        source_id="private-member",
                        title="Private member",
                        page_url="https://www.printables.com/model/2",
                    )
                ],
            )
        )
        db_session.rollback()

        response = client.post(
            f"/api/v1/ingest/collection/{token}/select",
            headers=headers,
            json={"member_ids": ["private-member"]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "collection_not_found"
        assert ingest_api.pending_collections.get(token) is not None


class TestListJobs:
    @pytest.mark.parametrize(
        "terminal_limit",
        [
            pytest.param(0, id="minimum"),
            pytest.param(100, id="maximum"),
        ],
    )
    def test_accepts_terminal_limit_boundaries(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        terminal_limit: int,
    ) -> None:
        response = client.get(
            "/api/v1/ingest/jobs",
            headers=auth_headers,
            params={"terminal_limit": terminal_limit},
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "terminal_limit",
        [
            pytest.param(-1, id="below-minimum"),
            pytest.param(101, id="above-maximum"),
        ],
    )
    def test_rejects_terminal_limits_outside_the_schema_bounds(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        terminal_limit: int,
    ) -> None:
        response = client.get(
            "/api/v1/ingest/jobs",
            headers=auth_headers,
            params={"terminal_limit": terminal_limit},
        )

        assert response.status_code == 422, response.text

    def test_marks_job_lists_as_non_cacheable(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/ingest/jobs", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"


class TestGetJob:
    def test_marks_job_status_as_non_cacheable(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        owner = db_session.exec(
            select(User).where(User.username == "test-writer")
        ).one()
        assert owner.id is not None
        job_id = registry.create(
            owner_user_id=owner.id, kind="model", session=db_session
        )
        db_session.commit()

        response = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"


class TestRouterAuthentication:
    @pytest.mark.parametrize(
        ("method", "path", "request_kwargs"),
        [
            pytest.param("POST", "/api/v1/ingest/orca", {}, id="orca"),
            pytest.param("POST", "/api/v1/ingest/model", {}, id="model"),
            pytest.param(
                "POST",
                "/api/v1/ingest/url",
                {"json": {"url": "https://example.com/a.stl"}},
                id="url",
            ),
            pytest.param("POST", "/api/v1/ingest/archive", {}, id="archive"),
            pytest.param(
                "POST", "/api/v1/ingest/archive/inspect", {}, id="archive-inspect"
            ),
            pytest.param(
                "POST",
                "/api/v1/ingest/archive/token/select",
                {"json": {"entry_ids": ["a"]}},
                id="archive-select",
            ),
            pytest.param(
                "POST",
                "/api/v1/ingest/url/files/token/select",
                {"json": {"file_ids": ["a"]}},
                id="file-select",
            ),
            pytest.param(
                "POST",
                "/api/v1/ingest/collection/token/select",
                {"json": {"member_ids": ["a"]}},
                id="collection-select",
            ),
            pytest.param("GET", "/api/v1/ingest/jobs", {}, id="job-list"),
            pytest.param("GET", "/api/v1/ingest/jobs/job", {}, id="job-detail"),
        ],
    )
    def test_requires_authentication_for_every_ingestion_route(
        self,
        client: TestClient,
        method: str,
        path: str,
        request_kwargs: dict[str, object],
    ) -> None:
        response = client.request(method, path, **request_kwargs)

        assert response.status_code == 401, response.text
