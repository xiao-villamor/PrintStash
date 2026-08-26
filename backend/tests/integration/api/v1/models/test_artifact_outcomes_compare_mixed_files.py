"""Defends artifact outcomes compare mixed files at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._model_revisions_api_shared import (
    FileType,
    Metadata,
    Path,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    _configure_storage,
    _file,
    _large_gcode,
    _model,
)


def test_artifact_outcomes_compare_mixed_files(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session, slug="outcomes")
    mesh = _file(db_session, model, file_type=FileType.STL, version=1, sha="c")
    gcode = _file(db_session, model, version=2, sha="d")
    db_session.add_all(
        [
            PrintJob(
                model_id=model.id,
                file_id=gcode.id,
                remote_filename=gcode.original_filename,
                state=PrintJobState.COMPLETED,
                source="manual",
                actual_duration_s=100,
                filament_g_effective=12.5,
                cost=1.25,
            ),
            PrintJob(
                model_id=model.id,
                file_id=gcode.id,
                remote_filename=gcode.original_filename,
                state=PrintJobState.FAILED,
                source="manual",
                actual_duration_s=50,
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        f"/api/v1/models/{model.id}/artifact-outcomes?file_id={mesh.id}&file_id={gcode.id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    by_id = {row["file_id"]: row for row in response.json()}
    assert by_id[mesh.id]["print_count"] == 0
    assert by_id[gcode.id]["completed_count"] == 1
    assert by_id[gcode.id]["failed_count"] == 1
    assert by_id[gcode.id]["success_rate"] == 0.5
    assert by_id[gcode.id]["average_duration_s"] == 75


def test_update_revision_status_notes_and_recommended(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={
            "revision_status": "known_good",
            "revision_notes": "Printed cleanly in PETG",
            "is_recommended": True,
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    revision = body["files"][0]
    assert revision["revision_status"] == "known_good"
    assert revision["revision_notes"] == "Printed cleanly in PETG"
    assert revision["is_recommended"] is True


def test_model_printer_files_lists_printers_for_gcode(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    db_session.add(
        PrinterFile(
            printer_id=printer.id,
            file_id=file_row.id,
            remote_filename="file-1.gcode",
            matched_by="upload_history",
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/models/{model.id}/printer-files", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "file_id": file_row.id,
            "printer_id": printer.id,
            "printer_name": "Ender",
            "remote_filename": "file-1.gcode",
            "matched_by": "upload_history",
            "last_seen_at": resp.json()[0]["last_seen_at"],
            "missing_since": None,
        }
    ]


def test_model_print_jobs_empty_when_none(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session)

    resp = client.get(f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_model_print_jobs_lists_history_enriched(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    file_row.revision_label = "PETG baseline"
    db_session.add(file_row)
    db_session.add(
        Metadata(file_id=file_row.id, slicer_name="OrcaSlicer", material_type="PETG")
    )
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    db_session.add(
        PrintJob(
            printer_id=printer.id,
            file_id=file_row.id,
            model_id=model.id,
            remote_filename="file-1.gcode",
            state=PrintJobState.COMPLETED,
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/models/{model.id}/print-jobs", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["printer_name"] == "Ender"
    assert rows[0]["state"] == "completed"
    assert rows[0]["material_type"] == "PETG"
    assert rows[0]["revision_label"] == "PETG baseline"
    assert rows[0]["gcode_revision_number"] == 1


def test_list_models_can_filter_by_printer(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    present_model = _model(db_session, slug="present")
    absent_model = _model(db_session, slug="absent")
    present_file = _file(db_session, present_model)
    _file(db_session, absent_model, sha="b")
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    db_session.add(
        PrinterFile(
            printer_id=printer.id,
            file_id=present_file.id,
            remote_filename="present.gcode",
            matched_by="upload_history",
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/models?printer_id={printer.id}", headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert [row["id"] for row in data] == [present_model.id]
    assert data[0]["printer_presence"] == [
        {
            "printer_id": printer.id,
            "printer_name": "Ender",
            "file_count": 1,
        }
    ]


def test_list_models_can_filter_by_missing_printer_presence(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    present_model = _model(db_session, slug="present-none")
    absent_model = _model(db_session, slug="absent-none")
    present_file = _file(db_session, present_model)
    _file(db_session, absent_model, sha="b")
    printer = Printer(name="Ender", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    db_session.add(
        PrinterFile(
            printer_id=printer.id,
            file_id=present_file.id,
            remote_filename="present.gcode",
            matched_by="upload_history",
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/models?printer_presence=none", headers=auth_headers)

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert absent_model.id in ids
    assert present_model.id not in ids


def test_export_models_json_includes_metadata_only_context(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    file_row.revision_label = "PETG baseline"
    file_row.revision_status = "known_good"
    file_row.is_recommended = True
    db_session.add(file_row)
    db_session.add(
        Metadata(
            file_id=file_row.id,
            slicer_name="OrcaSlicer",
            printer_model="Voron 2.4",
            layer_height_mm=0.2,
            material_type="PETG",
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/models/export", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["contents"]["kind"] == "metadata_only"
    assert body["counts"] == {"models": 1, "files": 1}
    exported_file = body["models"][0]["files"][0]
    assert exported_file["revision_label"] == "PETG baseline"
    assert exported_file["revision_status"] == "known_good"
    assert exported_file["is_recommended"] is True
    assert exported_file["metadata"]["slicer_name"] == "OrcaSlicer"
    assert exported_file["metadata"]["printer_model"] == "Voron 2.4"


def test_export_models_csv_flattens_one_row_per_file(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    db_session.add(
        Metadata(
            file_id=file_row.id,
            slicer_name="PrusaSlicer",
            infill_percent=20,
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/models/export?format=csv", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "model_name" in resp.text
    assert "file_id" in resp.text
    assert "file_type" in resp.text
    assert "Bracket" in resp.text
    assert "PrusaSlicer" in resp.text


def test_add_gcode_revision_to_existing_model(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)
    first = _file(db_session, model, version=1)
    first.is_recommended = True
    db_session.add(first)
    db_session.commit()

    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        headers=auth_headers,
        files={
            "file": (
                "tighter-fit.gcode",
                b"; generated by OrcaSlicer\nG28\n",
                "text/plain",
            )
        },
        data={
            "revision_label": "Tighter fit",
            "revision_notes": "Reduced clearance",
            "is_recommended": "true",
        },
    )

    assert resp.status_code == 200, resp.text
    gcode_files = [f for f in resp.json()["files"] if f["file_type"] == "gcode"]
    assert [f["gcode_revision_number"] for f in gcode_files] == [1, 2]
    latest = gcode_files[-1]
    assert latest["revision_label"] == "Tighter fit"
    assert latest["revision_status"] == "needs_test"
    assert latest["revision_notes"] == "Reduced clearance"
    assert latest["is_recommended"] is True
    db_session.refresh(first)
    assert first.is_recommended is False


def test_add_gcode_revision_accepts_payload_over_nginx_default_limit(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)

    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        headers=auth_headers,
        files={
            "file": (
                "large-revision.gcode",
                _large_gcode(),
                "text/plain",
            )
        },
        data={"revision_label": "Large revision"},
    )

    assert resp.status_code == 200, resp.text
    gcode_files = [f for f in resp.json()["files"] if f["file_type"] == "gcode"]
    assert len(gcode_files) == 1
    assert gcode_files[0]["size_bytes"] > 1_000_000
    assert gcode_files[0]["revision_label"] == "Large revision"


def test_first_gcode_revision_is_auto_recommended(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)

    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        headers=auth_headers,
        files={
            "file": ("first.gcode", b"; generated by OrcaSlicer\nG28\n", "text/plain")
        },
        data={"is_recommended": "false"},
    )

    assert resp.status_code == 200, resp.text
    gcode_files = [f for f in resp.json()["files"] if f["file_type"] == "gcode"]
    assert len(gcode_files) == 1
    assert gcode_files[0]["is_recommended"] is True


def test_second_unmarked_gcode_revision_keeps_existing_recommended(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)
    first = _file(db_session, model, version=1)
    first.is_recommended = True
    db_session.add(first)
    db_session.commit()

    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        headers=auth_headers,
        files={
            "file": ("second.gcode", b"; generated by OrcaSlicer\nG28\n", "text/plain")
        },
        data={"is_recommended": "false"},
    )

    assert resp.status_code == 200, resp.text
    files = {f["id"]: f for f in resp.json()["files"]}
    assert files[first.id]["is_recommended"] is True
    recommended = [f for f in files.values() if f["is_recommended"]]
    assert len(recommended) == 1


def test_add_gcode_revision_rejects_non_gcode(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    _configure_storage(tmp_path)
    model = _model(db_session)

    resp = client.post(
        f"/api/v1/models/{model.id}/gcode-revisions",
        headers=auth_headers,
        files={"file": ("part.stl", b"solid part\nendsolid part\n", "application/sla")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_file_type"


def test_update_revision_can_clear_status_notes_and_recommended(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)
    file_row.revision_status = "known_good"
    file_row.revision_notes = "Old note"
    file_row.is_recommended = True
    db_session.add(file_row)
    db_session.commit()

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={
            "revision_label": "PETG baseline",
            "revision_status": None,
            "revision_notes": "   ",
            "is_recommended": False,
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200
    revision = resp.json()["files"][0]
    assert revision["revision_label"] == "PETG baseline"
    assert revision["revision_status"] is None
    assert revision["revision_notes"] is None
    assert revision["is_recommended"] is False


def test_update_revision_requires_auth(client: TestClient, db_session: Session) -> None:
    model = _model(db_session)
    file_row = _file(db_session, model)

    resp = client.patch(
        f"/api/v1/models/{model.id}/files/{file_row.id}/revision",
        json={"revision_status": "failed"},
    )

    assert resp.status_code == 401


def test_delete_revision_soft_deletes_and_hides_it(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    model = _model(db_session)
    keep = _file(db_session, model, version=1, sha="a")
    drop = _file(db_session, model, version=2, sha="b")
    drop.is_recommended = True
    db_session.add(drop)
    db_session.commit()

    resp = client.delete(
        f"/api/v1/models/{model.id}/files/{drop.id}/revision",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    file_ids = [f["id"] for f in resp.json()["files"]]
    assert file_ids == [keep.id]

    db_session.refresh(drop)
    assert drop.deleted_at is not None
    assert drop.is_recommended is False


def test_delete_recommended_revision_promotes_newest_remaining(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    """Invariant: a model with G-code always keeps exactly one recommended
    revision. Deleting the recommended one must promote the newest survivor —
    not leave the model with G-code and nothing recommended."""
    model = _model(db_session)
    _oldest = _file(db_session, model, version=1, sha="a")
    middle = _file(db_session, model, version=2, sha="b")
    newest = _file(db_session, model, version=3, sha="c")
    middle.is_recommended = True
    db_session.add(middle)
    db_session.commit()

    resp = client.delete(
        f"/api/v1/models/{model.id}/files/{middle.id}/revision",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    files = {f["id"]: f for f in resp.json()["files"]}
    recommended = [fid for fid, f in files.items() if f["is_recommended"]]
    assert recommended == [newest.id], "newest live revision should be promoted"
    # Exactly one recommended, and it is a live file.
    assert len(recommended) == 1
