"""Single-behaviour router contracts used by the models coverage matrix."""

from app.core.config import _overlay

from ._model_revisions_api_shared import (
    FileType,
    Session,
    TestClient,
    _file,
    _headers_for,
    _model,
    _regular_user,
    utcnow,
)


def test_rejects_an_unsupported_metadata_export_format(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/models/export?format=xml", headers=auth_headers)

    assert response.status_code == 422, response.text


def test_rejects_unauthenticated_library_archive_import(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models/library-import",
        files={"file": ("library.zip", b"not-read-before-auth", "application/zip")},
    )

    assert response.status_code == 401, response.text


def test_returns_zeroed_vault_statistics_for_an_empty_vault(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/models/stats", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["model_count"] == 0
    assert response.json()["file_count"] == 0


def test_excludes_live_models_from_trash(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    live = _model(db_session, slug="live-trash-exclusion")

    response = client.get("/api/v1/models/trash", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert live.id not in {row["id"] for row in response.json()}


def test_rejects_malformed_trash_pagination(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/models/trash?limit=0", headers=auth_headers)

    assert response.status_code == 422, response.text


def test_makes_expired_trash_purge_idempotent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.delete("/api/v1/models/trash/expired", headers=auth_headers)
    assert first.status_code == 200, first.text

    second = client.delete("/api/v1/models/trash/expired", headers=auth_headers)

    assert second.status_code == 200, second.text
    assert second.json()["purged_count"] == 0


def test_rejects_non_superuser_expired_trash_purge(
    client: TestClient, db_session: Session
) -> None:
    user = _regular_user(db_session, "trash-purge-reader")

    response = client.delete("/api/v1/models/trash/expired", headers=_headers_for(user))

    assert response.status_code == 403, response.text


def test_hides_an_unavailable_model_from_printer_file_presence(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/models/999999/printer-files", headers=auth_headers)

    assert response.status_code == 404, response.text


def test_hides_an_unavailable_model_from_print_job_history(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/models/999999/print-jobs", headers=auth_headers)

    assert response.status_code == 404, response.text


def test_limits_artifact_outcomes_to_one_requested_file(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session, slug="one-outcome")
    first = _file(db_session, model, file_type=FileType.GCODE, sha="1")
    _file(db_session, model, file_type=FileType.STL, version=2, sha="2")

    response = client.get(
        f"/api/v1/models/{model.id}/artifact-outcomes?file_id={first.id}",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert [row["file_id"] for row in response.json()] == [first.id]


def test_restoring_a_live_model_is_stable(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session, slug="already-live")

    response = client.post(f"/api/v1/models/{model.id}/restore", headers=auth_headers)

    assert response.status_code == 200, response.text
    db_session.refresh(model)
    assert model.deleted_at is None


def test_repeated_model_soft_delete_is_rejected(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session, slug="delete-twice")
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.commit()

    response = client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)

    assert response.status_code == 404, response.text


def test_soft_delete_preserves_artifact_bytes(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, tmp_path
) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    model = _model(db_session, slug="preserve-bytes")
    artifact = _file(db_session, model, file_type=FileType.STL, sha="p")
    path = tmp_path / "files" / "preserve-bytes" / "v1" / "file-1.stl"
    artifact.path = str(path)
    db_session.add(artifact)
    db_session.commit()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"solid preserve")

    response = client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)

    assert response.status_code == 204, response.text
    assert path.read_bytes() == b"solid preserve"
