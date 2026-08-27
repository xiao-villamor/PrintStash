"""Defends force rebuild refreshes existing mesh thumbnail at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._ingest_api_shared import (
    WEBP_MAGIC,
    Collection,
    CollectionPermission,
    CollectionRole,
    Path,
    Session,
    TestClient,
    _completed_job,
    _configure_storage,
    _cube_stl,
    _headers_for,
    _post_empty_filename_multipart,
    _regular_user,
    io,
)


def test_force_rebuild_refreshes_existing_mesh_thumbnail(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    from PIL import Image

    _configure_storage(tmp_path)

    payload = _completed_job(
        client,
        client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("cube.stl", _cube_stl(), "application/sla")},
            data={"model_name": "Cube"},
        ),
    )
    file_id = payload["file_id"]
    model_id = payload["model_id"]
    replacement_buffer = io.BytesIO()
    Image.new("RGB", (12, 10), (220, 30, 20)).save(replacement_buffer, format="PNG")
    replacement = replacement_buffer.getvalue()

    monkeypatch.setattr(
        "app.services.mesh_processing.render_thumbnail",
        lambda _path: replacement,
    )

    response = client.post(
        "/api/v1/files/thumbnails/rebuild?force=true",
        headers=auth_headers,
    )

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200, job.text
    payload = job.json()
    assert payload["state"] == "completed", payload
    assert payload["completion"] == "complete"
    assert payload["thumbnail_status"] == "generated"
    assert payload["succeeded"] == 1
    assert payload["result"]["rebuilt"] == [model_id]

    thumbnail = client.get(f"/api/v1/files/{file_id}/thumbnail", headers=auth_headers)
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.content.startswith(WEBP_MAGIC)
    with Image.open(io.BytesIO(thumbnail.content)) as refreshed:
        assert refreshed.convert("RGB").getpixel((0, 0)) == (220, 30, 20)


def test_ingest_orca_rejects_missing_filename(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = _post_empty_filename_multipart(
        client, "/api/v1/ingest/orca", auth_headers
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "filename_required"


def test_ingest_orca_rejects_unsupported_suffix(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/orca",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "unsupported_file_type"


def test_ingest_model_rejects_missing_filename(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = _post_empty_filename_multipart(
        client, "/api/v1/ingest/model", auth_headers
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "filename_required"


def test_ingest_model_rejects_unsupported_suffix(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "unsupported_file_type"


def test_ingest_model_requires_collection_for_non_superuser(
    tmp_path: Path, client: TestClient, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    user = _regular_user(db_session)
    response = client.post(
        "/api/v1/ingest/model",
        headers=_headers_for(user),
        files={"file": ("cube.stl", _cube_stl(), "application/sla")},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "collection_required"


def test_ingest_model_unknown_collection_denied_for_non_superuser(
    tmp_path: Path, client: TestClient, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    user = _regular_user(db_session)
    response = client.post(
        "/api/v1/ingest/model",
        headers=_headers_for(user),
        files={"file": ("cube.stl", _cube_stl(), "application/sla")},
        data={"collection": "does-not-exist"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "collection_permission_denied"


def test_ingest_model_requires_role_on_existing_collection(
    tmp_path: Path, client: TestClient, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    user = _regular_user(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    response = client.post(
        "/api/v1/ingest/model",
        headers=_headers_for(user),
        files={"file": ("cube.stl", _cube_stl(), "application/sla")},
        data={"collection": "brackets"},
    )
    assert response.status_code == 403, response.text


def test_ingest_model_succeeds_with_granted_collection_role(
    tmp_path: Path, client: TestClient, db_session: Session
) -> None:
    _configure_storage(tmp_path)
    user = _regular_user(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=collection.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    payload = _completed_job(
        client,
        client.post(
            "/api/v1/ingest/model",
            headers=_headers_for(user),
            files={"file": ("cube.stl", _cube_stl(), "application/sla")},
            data={"collection": "brackets"},
        ),
    )
    assert payload["model_id"] is not None


def test_ingest_model_rejects_disabled_target_library(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("cube.stl", _cube_stl(), "application/sla")},
        data={"target_library_id": "999"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "external_libraries_disabled"


def test_ingest_jobs_list_and_get_scoped_to_owner(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    payload = _completed_job(
        client,
        client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("cube.stl", _cube_stl(), "application/sla")},
            data={"model_name": "Cube"},
        ),
    )
    jobs = client.get("/api/v1/ingest/jobs", headers=auth_headers)
    assert jobs.status_code == 200, jobs.text
    assert any(j["model_id"] == payload["model_id"] for j in jobs.json())

    other = _regular_user(db_session, "other-owner")
    forbidden = client.get(
        f"/api/v1/ingest/jobs/{jobs.json()[0]['job_id']}", headers=_headers_for(other)
    )
    assert forbidden.status_code == 404, forbidden.text


def test_get_job_unknown_id_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/ingest/jobs/does-not-exist", headers=auth_headers)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "job_not_found"
