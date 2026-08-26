"""Defends delete model soft deletes at the models API integration boundary.

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
    io,
    pytest,
    utcnow,
    zipfile,
)


def test_delete_model_soft_deletes(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.delete(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert resp.status_code == 204
    db_session.refresh(model)
    assert model.deleted_at is not None


def test_restore_model_and_purge_model(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.commit()

    restored = client.post(f"/api/v1/models/{model.id}/restore", headers=auth_headers)
    assert restored.status_code == 200
    db_session.refresh(model)
    assert model.deleted_at is None

    model.deleted_at = utcnow()
    db_session.add(model)
    db_session.commit()
    purged = client.delete(f"/api/v1/models/{model.id}/purge", headers=auth_headers)
    assert purged.status_code == 200
    assert purged.json()["purged_model_ids"] == [model.id]


def test_restore_model_unknown_id_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post("/api/v1/models/999999/restore", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "model_not_found"


def test_purge_model_unknown_id_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.delete("/api/v1/models/999999/purge", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "model_not_found"


def test_purge_model_not_in_trash_rejected(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.delete(f"/api/v1/models/{model.id}/purge", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "model_not_in_trash"


def test_batch_move_models_to_new_collection(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    a = _model(db_session, slug="batch-a")
    b = _model(db_session, slug="batch-b")

    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [a.id, b.id], "collection": "new/spot"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["succeeded_ids"]) == {a.id, b.id}
    assert body["failed_count"] == 0


def test_batch_move_models_root_requires_superuser(
    client: TestClient, db_session: Session
) -> None:
    user = _regular_user(db_session)
    model = _model(db_session)
    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": ""},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "root_collection_admin_required"


def test_batch_move_models_reports_missing_model(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [999999], "collection": "somewhere"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_batch_tag_models_adds_and_removes(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    a = _model(db_session, slug="tag-a")
    b = _model(db_session, slug="tag-b")

    added = client.post(
        "/api/v1/models/batch/tags",
        json={"model_ids": [a.id, b.id], "add": ["shiny"], "remove": []},
        headers=auth_headers,
    )
    assert added.status_code == 200
    assert added.json()["succeeded_count"] == 2

    removed = client.post(
        "/api/v1/models/batch/tags",
        json={"model_ids": [a.id], "add": [], "remove": ["shiny"]},
        headers=auth_headers,
    )
    assert removed.status_code == 200
    detail = client.get(f"/api/v1/models/{a.id}", headers=auth_headers).json()
    assert detail["tags"] == []


def test_batch_set_revision_labels(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.patch(
        "/api/v1/models/batch/revision-labels",
        json={"file_ids": [file_row.id], "revision_label": "Batch label"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded_ids"] == [file_row.id]
    db_session.refresh(file_row)
    assert file_row.revision_label == "Batch label"


def test_batch_set_revision_labels_rejects_non_gcode(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    mesh = _file(db_session, model, file_type=FileType.STL)

    resp = client.patch(
        "/api/v1/models/batch/revision-labels",
        json={"file_ids": [mesh.id], "revision_label": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "revision_not_supported"


def test_batch_set_revision_labels_unknown_file_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.patch(
        "/api/v1/models/batch/revision-labels",
        json={"file_ids": [999999], "revision_label": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_not_found"


def test_batch_delete_models(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.post(
        "/api/v1/models/batch/delete",
        json={"model_ids": [model.id]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded_ids"] == [model.id]
    db_session.refresh(model)
    assert model.deleted_at is not None


def test_library_archive_export_and_import_round_trip(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    # Real ingest so the exported artifact has an actual on-disk blob (the
    # archive writer stats + reads every file from storage).
    uploaded = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("cube.stl", b"solid cube\nendsolid cube\n", "application/sla")},
        data={"model_name": "Archive Me"},
    )
    assert uploaded.status_code == 202, uploaded.text
    job = client.get(
        f"/api/v1/ingest/jobs/{uploaded.json()['job_id']}", headers=auth_headers
    )
    assert job.json()["state"] == "completed", job.json()

    export = client.get("/api/v1/models/library-archive", headers=auth_headers)
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"

    import_resp = client.post(
        "/api/v1/models/library-import",
        headers=auth_headers,
        files={
            "file": ("printstash-library-v1.zip", export.content, "application/zip")
        },
    )
    assert import_resp.status_code == 202
    imported_job = client.get(
        f"/api/v1/ingest/jobs/{import_resp.json()['job_id']}", headers=auth_headers
    )
    assert imported_job.json()["state"] == "completed"


def test_library_import_rejects_non_zip(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/models/library-import",
        headers=auth_headers,
        files={"file": ("archive.tar", b"not a zip", "application/x-tar")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "archive_zip_required"


def test_library_import_reports_invalid_archive_in_job(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr("not-a-manifest.txt", "hello")

    resp = client.post(
        "/api/v1/models/library-import",
        headers=auth_headers,
        files={"file": ("bad.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 202
    status = client.get(
        f"/api/v1/ingest/jobs/{resp.json()['job_id']}", headers=auth_headers
    )
    assert status.status_code == 200
    assert status.json()["state"] == "failed"
    assert status.json()["error"] == "portable_manifest_invalid"


def test_star_unknown_model_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put("/api/v1/models/999999/star", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "model_not_found"


def test_add_gcode_revision_cleans_up_staged_file_on_error(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)
    staged_paths: list[Path] = []

    def fake_add_gcode_revision_to_model(**kwargs):
        staged_paths.append(kwargs["staged_path"])
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.api.v1.models.add_gcode_revision_to_model",
        fake_add_gcode_revision_to_model,
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            f"/api/v1/models/{model.id}/gcode-revisions",
            headers=auth_headers,
            files={"file": ("rev.gcode", b"G28\n", "text/plain")},
        )
    assert staged_paths and not staged_paths[0].exists()


def test_manual_print_job_unknown_file_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={"file_id": 999999, "printer_name": "Bench"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "file_not_found"


def test_manual_print_job_unknown_printer_id_404(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={"file_id": file_row.id, "printer_id": 999999},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "printer_not_found"


def test_manual_print_job_with_registered_printer(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)

    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={"file_id": file_row.id, "printer_id": printer.id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["printer_id"] == printer.id
    assert resp.json()["printer_name"] == "Ender"


def test_manual_print_job_invalid_state_is_rejected(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={
            "file_id": file_row.id,
            "printer_name": "Bench",
            "state": "not_a_real_state",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_manual_print_job_rejects_trashed_file(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    file_row.deleted_at = utcnow()
    db_session.add(file_row)
    db_session.commit()

    response = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={"file_id": file_row.id, "printer_name": "Bench"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "file_not_found"


def test_batch_move_root_model_denied_for_non_superuser(
    client: TestClient, db_session: Session
) -> None:
    model = _model(db_session)  # root model, no collection
    dest = Collection(name="Dest", slug="dest", path="dest")
    db_session.add(dest)
    db_session.commit()
    db_session.refresh(dest)
    user = _regular_user(db_session)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=dest.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": "dest"},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "root_collection_admin_required"


def test_batch_move_unknown_destination_denied_for_non_superuser(
    client: TestClient, db_session: Session
) -> None:
    user = _regular_user(db_session)
    model = _model(db_session)
    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": "does/not/exist"},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "collection_permission_denied"


def test_batch_move_to_root_as_superuser(
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

    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": ""},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    db_session.refresh(model)
    assert model.collection_id is None


def test_batch_move_into_existing_destination_checks_role(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    collection = Collection(name="Brackets", slug="brackets", path="brackets")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": "brackets"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    db_session.refresh(model)
    assert model.collection_id == collection.id


def test_batch_move_denies_model_in_uneditable_source_collection(
    client: TestClient, db_session: Session
) -> None:
    source = Collection(name="Source", slug="source", path="source")
    dest = Collection(name="Dest", slug="dest", path="dest")
    db_session.add(source)
    db_session.add(dest)
    db_session.commit()
    db_session.refresh(source)
    db_session.refresh(dest)
    model = _model(db_session, slug="stuck")
    model.collection_id = source.id
    db_session.add(model)
    db_session.commit()

    user = _regular_user(db_session)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=dest.id, role=CollectionRole.EDIT
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/models/batch/move",
        json={"model_ids": [model.id], "collection": "dest"},
        headers=_headers_for(user),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "collection_permission_denied"


def test_batch_tag_models_skips_blank_remove_entries(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    resp = client.post(
        "/api/v1/models/batch/tags",
        json={"model_ids": [model.id], "add": [], "remove": ["   "]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded_count"] == 1


def test_batch_set_revision_labels_reports_unexpected_error(
    client: TestClient, auth_headers: dict[str, str], db_session: Session, monkeypatch
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    def fake_set_revision_labels(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.api.v1.models.model_views.set_revision_labels", fake_set_revision_labels
    )
    with pytest.raises(RuntimeError, match="boom"):
        client.patch(
            "/api/v1/models/batch/revision-labels",
            json={"file_ids": [file_row.id], "revision_label": "x"},
            headers=auth_headers,
        )


def test_update_model_clears_source_url(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    model.source_url = "https://example.com/original"
    db_session.add(model)
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}",
        json={"source_url": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["source_url"] is None
