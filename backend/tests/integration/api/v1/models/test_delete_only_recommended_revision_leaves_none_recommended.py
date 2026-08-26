"""Defends delete only recommended revision leaves none recommended at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._model_revisions_api_shared import (
    Collection,
    CollectionPermission,
    CollectionRole,
    FileType,
    Path,
    Printer,
    Session,
    TestClient,
    _configure_storage,
    _file,
    _headers_for,
    _model,
    _regular_user,
    utcnow,
)


def test_delete_only_recommended_revision_leaves_none_recommended(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    """Deleting the sole G-code revision is allowed and simply leaves the model
    with no recommended revision (no G-code remains to carry the marker)."""
    model = _model(db_session)
    only = _file(db_session, model, version=1, sha="a")
    only.is_recommended = True
    db_session.add(only)
    db_session.commit()

    resp = client.delete(
        f"/api/v1/models/{model.id}/files/{only.id}/revision",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["files"] == []
    db_session.refresh(only)
    assert only.deleted_at is not None
    assert only.is_recommended is False


def test_delete_revision_rejects_non_gcode(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    mesh = _file(db_session, model, file_type=FileType.STL)

    resp = client.delete(
        f"/api/v1/models/{model.id}/files/{mesh.id}/revision",
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "revision_not_supported"


def test_delete_revision_requires_auth(client: TestClient, db_session: Session) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.delete(f"/api/v1/models/{model.id}/files/{file_row.id}/revision")

    assert resp.status_code == 401


def test_manual_print_job_accepts_adhoc_printer_name(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={
            "printer_name": "Garage Prusa",
            "file_id": file_row.id,
            "state": "completed",
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["printer_id"] is None
    assert body["printer_name"] == "Garage Prusa"

    listed = client.get(
        f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers
    ).json()
    assert listed[0]["printer_name"] == "Garage Prusa"


def test_manual_print_job_requires_a_printer(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={"file_id": file_row.id, "printer_name": "   "},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "printer_required"


def test_update_revision_rejects_invalid_status(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={"revision_status": "perfect_enough"},
        headers=auth_headers,
    )

    assert resp.status_code == 422


def test_update_revision_rejects_non_gcode(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model, file_type=FileType.STL)

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={"revision_status": "known_good"},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "revision_not_supported"


def test_update_revision_rejects_soft_deleted_file(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    file_row.deleted_at = utcnow()
    db_session.add(file_row)
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={"revision_status": "failed"},
        headers=auth_headers,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_recommended_revision_is_unique_per_model(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    first = _file(db_session, model, version=1, sha="a")
    second = _file(db_session, model, version=2, sha="b")

    resp1 = client.patch(
        f"/api/v1/models/{model.id}/files/{first.id}/revision",
        json={"is_recommended": True},
        headers=auth_headers,
    )
    assert resp1.status_code == 200

    resp2 = client.patch(
        f"/api/v1/models/{model.id}/files/{second.id}/revision",
        json={"is_recommended": True},
        headers=auth_headers,
    )
    assert resp2.status_code == 200

    files = {f["id"]: f for f in resp2.json()["files"]}
    assert files[first.id]["is_recommended"] is False
    assert files[second.id]["is_recommended"] is True


def test_recommended_revision_does_not_clear_other_models(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    first_model = _model(db_session, slug="one")
    second_model = _model(db_session, slug="two")
    first_file = _file(db_session, first_model, version=1, sha="a")
    second_file = _file(db_session, second_model, version=1, sha="b")
    first_file.is_recommended = True
    db_session.add(first_file)
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{second_model.id}/files/{second_file.id}/revision",
        json={"is_recommended": True},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    db_session.refresh(first_file)
    db_session.refresh(second_file)
    assert first_file.is_recommended is True
    assert second_file.is_recommended is True


def test_revision_file_must_belong_to_model(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    first_model = _model(db_session, slug="one")
    second_model = _model(db_session, slug="two")
    file_row = _file(db_session, second_model)

    resp = client.patch(
        f"/api/v1/models/{first_model.id}/files/{file_row.id}/revision",
        json={"revision_status": "failed"},
        headers=auth_headers,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_get_model_returns_detail(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == model.id


def test_get_model_unknown_id_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/models/999999", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "model_not_found"


def test_list_models_requires_superuser_for_printer_filters(
    client: TestClient, db_session: Session
) -> None:
    user = _regular_user(db_session)
    resp = client.get("/api/v1/models?printer_presence=any", headers=_headers_for(user))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_required"


def test_star_and_unstar_model(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    starred = client.put(f"/api/v1/models/{model.id}/star", headers=auth_headers)
    assert starred.status_code == 200
    assert starred.json() == {"model_id": model.id, "starred": True}
    # Idempotent — starring twice does not error or duplicate the row.
    again = client.put(f"/api/v1/models/{model.id}/star", headers=auth_headers)
    assert again.status_code == 200

    unstarred = client.delete(f"/api/v1/models/{model.id}/star", headers=auth_headers)
    assert unstarred.status_code == 200
    assert unstarred.json() == {"model_id": model.id, "starred": False}


def test_artifact_outcomes_missing_file_returns_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    real = _file(db_session, model, file_type=FileType.STL, sha="e")
    resp = client.get(
        f"/api/v1/models/{model.id}/artifact-outcomes?file_id={real.id}&file_id=999999",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_add_gcode_revision_rejects_missing_filename(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)
    boundary = "revisionboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename=""\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hi\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        content=body,
        headers={
            **auth_headers,
            "content-type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "filename_required"


def test_delete_file_revision_rejects_mismatched_model(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    first_model = _model(db_session, slug="one")
    second_model = _model(db_session, slug="two")
    file_row = _file(db_session, second_model)

    resp = client.delete(
        f"/api/v1/models/{first_model.id}/files/{file_row.id}/revision",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_import_print_jobs_from_printer_requires_moonraker_url(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    printer = Printer(name="No URL")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs/import-printer/{printer.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "printer_no_url"


def test_import_print_jobs_from_printer_unknown_printer_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs/import-printer/999999",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "printer_not_found"


def test_import_print_jobs_from_printer_reports_moonraker_error(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, monkeypatch
) -> None:
    model = _model(db_session)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    from app.services.moonraker import MoonrakerError

    async def fake_import(*_a, **_k):
        raise MoonrakerError("unreachable")

    monkeypatch.setattr(
        "app.api.v1.models.job_import.import_print_jobs_from_printer", fake_import
    )
    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs/import-printer/{printer.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "printer_unreachable"


def test_vault_stats_returns_counts(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    _model(db_session)
    resp = client.get("/api/v1/models/stats", headers=auth_headers)
    assert resp.status_code == 200
    assert "model_count" in resp.json() or resp.json() != {}


def test_print_stats_requires_superuser(
    client: TestClient, db_session: Session
) -> None:
    user = _regular_user(db_session)
    resp = client.get("/api/v1/models/stats/prints", headers=_headers_for(user))
    assert resp.status_code == 403


def test_print_stats_returns_window(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    resp = client.get("/api/v1/models/stats/prints?period=7d", headers=auth_headers)
    assert resp.status_code == 200


def test_list_trash_and_purge_expired(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.commit()

    trash = client.get("/api/v1/models/trash", headers=auth_headers)
    assert trash.status_code == 200
    assert any(row["id"] == model.id for row in trash.json())

    purged = client.delete("/api/v1/models/trash/expired", headers=auth_headers)
    assert purged.status_code == 200
    assert "purged_count" in purged.json()


def test_update_model_can_move_to_root_as_superuser(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model.collection_id = collection.id
    db_session.add(model)
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"collection": ""},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["collection"] is None


def test_update_model_root_move_forbidden_for_non_superuser(
    client: TestClient, db_session: Session
) -> None:
    model = _model(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model.collection_id = collection.id
    db_session.add(model)
    db_session.commit()
    user = _regular_user(db_session)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=collection.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"collection": ""},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "root_collection_admin_required"


def test_update_model_moves_into_existing_collection(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"collection": "brackets", "name": "Renamed", "description": "desc"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_id"] == collection.id
    assert body["name"] == "Renamed"
    assert body["description"] == "desc"


def test_update_model_creates_missing_collection_as_superuser(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"collection": "brand/new/path"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["collection"] == "brand/new/path"


def test_update_model_denies_missing_collection_for_non_superuser(
    client: TestClient, db_session: Session
) -> None:
    model = _model(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model.collection_id = collection.id
    db_session.add(model)
    db_session.commit()
    user = _regular_user(db_session)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=collection.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"collection": "does/not/exist"},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "collection_permission_denied"


def test_update_model_replaces_tags(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"tags": ["functional", "bracket"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert set(resp.json()["tags"]) == {"functional", "bracket"}

    cleared = client.patch(
        f"/api/v1/models/{model.id}",
        json={"tags": []},
        headers=auth_headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["tags"] == []
