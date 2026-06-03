"""Tests for G-code revision metadata on model files."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import File, FileType, Model, Printer, PrinterFile


def _model(db_session: Session, *, slug: str = "bracket") -> Model:
    model = Model(name="Bracket", slug=slug, hash=f"{slug:0<64}"[:64])
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _file(
    db_session: Session,
    model: Model,
    *,
    file_type: FileType = FileType.GCODE,
    version: int = 1,
    sha: str = "a",
) -> File:
    file_row = File(
        model_id=model.id,
        path=f"/data/files/{model.slug}/v{version}/file-{version}.gcode",
        original_filename=f"file-{version}.gcode",
        file_type=file_type,
        version=version,
        size_bytes=123,
        sha256=sha * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)
    return file_row


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"


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
    client: TestClient, db_session: Session
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

    resp = client.get(f"/api/v1/models/{model.id}/printer-files")
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


def test_list_models_can_filter_by_printer(
    client: TestClient, db_session: Session
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

    resp = client.get(f"/api/v1/models?printer_id={printer.id}")

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
    client: TestClient, db_session: Session
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

    resp = client.get("/api/v1/models?printer_presence=none")

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert absent_model.id in ids
    assert present_model.id not in ids


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
