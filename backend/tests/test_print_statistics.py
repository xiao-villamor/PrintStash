"""Tests for the print statistics aggregation endpoint."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileType,
    FilamentProfile,
    Metadata,
    Model,
    PrintJob,
    PrintJobState,
)


def _model(
    db_session: Session, *, slug: str, collection_id: int | None = None
) -> Model:
    model = Model(
        name=slug.title(),
        slug=slug,
        hash=f"{slug:0<64}"[:64],
        collection_id=collection_id,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _file_with_material(
    db_session: Session,
    model: Model,
    *,
    sha: str,
    material_type: str,
    material_brand: str | None = None,
) -> File:
    file_row = File(
        model_id=model.id,
        path=f"/data/files/{model.slug}/{sha}.gcode",
        original_filename=f"{sha}.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=123,
        sha256=sha * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)
    db_session.add(
        Metadata(
            file_id=file_row.id,
            material_type=material_type,
            material_brand=material_brand,
        )
    )
    db_session.commit()
    return file_row


def _job(
    db_session: Session,
    model: Model,
    file_row: File,
    *,
    state: PrintJobState = PrintJobState.COMPLETED,
    grams: float | None = None,
    duration_s: int | None = None,
    finished_days_ago: float = 1,
) -> None:
    db_session.add(
        PrintJob(
            file_id=file_row.id,
            model_id=model.id,
            remote_filename=file_row.original_filename,
            state=state,
            filament_used_g=grams,
            actual_duration_s=duration_s,
            finished_at=utcnow() - timedelta(days=finished_days_ago),
        )
    )
    db_session.commit()


def test_print_stats_empty(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["period"] == "30d"
    assert body["total_prints"] == 0
    assert body["total_cost"] is None
    assert body["top_collections"] == []
    assert body["top_filaments"] == []


def test_print_stats_aggregates_completed(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    db_session.add(
        FilamentProfile(name="PETG", material_type="PETG", cost_per_kg=20.0)
    )
    db_session.commit()

    collection = Collection(name="Functional", slug="functional", path="functional")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    model = _model(db_session, slug="bracket", collection_id=collection.id)
    file_row = _file_with_material(
        db_session, model, sha="a", material_type="PETG"
    )
    # Two completed PETG prints: 100g @ 20/kg => 2.0 each.
    _job(db_session, model, file_row, grams=100.0, duration_s=3600)
    _job(db_session, model, file_row, grams=100.0, duration_s=1800)
    # A failed job and an out-of-window job must be excluded.
    _job(db_session, model, file_row, grams=100.0, state=PrintJobState.FAILED)
    _job(db_session, model, file_row, grams=100.0, finished_days_ago=60)

    resp = client.get("/api/v1/models/stats/prints?period=7d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_prints"] == 2
    assert body["total_filament_g"] == 200.0
    assert body["avg_filament_g"] == 100.0
    assert body["total_cost"] == 4.0
    assert body["total_print_time_s"] == 5400

    assert body["top_collections"][0]["name"] == "Functional"
    assert body["top_collections"][0]["print_count"] == 2
    assert body["top_collections"][0]["total_cost"] == 4.0

    assert body["top_filaments"][0]["material_type"] == "PETG"
    assert body["top_filaments"][0]["print_count"] == 2
    assert body["top_filaments"][0]["total_g"] == 200.0

    assert sum(b["print_count"] for b in body["cost_over_time"]) == 2


def test_print_stats_uncategorized_collection(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session, slug="loose")
    file_row = _file_with_material(db_session, model, sha="b", material_type="PLA")
    _job(db_session, model, file_row, grams=50.0)

    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["top_collections"][0]["name"] == "Uncategorized"
    assert body["top_collections"][0]["collection_id"] is None


def test_print_stats_falls_back_to_slicer_estimate(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    # A Bambu/manual job with no measured filament — stats must use the slicer
    # estimate stored on Metadata instead of reading as "—".
    model = _model(db_session, slug="estimated")
    file_row = File(
        model_id=model.id,
        path=f"/data/files/{model.slug}/c.gcode",
        original_filename="c.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=123,
        sha256="c" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)
    db_session.add(
        Metadata(
            file_id=file_row.id,
            material_type="PLA",
            filament_weight_g=75.0,
            filament_cost=1.5,
            estimated_time_s=1200,
        )
    )
    db_session.add(
        PrintJob(
            file_id=file_row.id,
            model_id=model.id,
            remote_filename="c.gcode",
            state=PrintJobState.COMPLETED,
            filament_used_g=None,
            actual_duration_s=None,
            finished_at=utcnow() - timedelta(days=1),
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_filament_g"] == 75.0
    assert body["avg_filament_g"] == 75.0
    assert body["total_cost"] == 1.5
    assert body["total_print_time_s"] == 1200


def test_print_stats_requires_admin(client: TestClient) -> None:
    resp = client.get("/api/v1/models/stats/prints")
    assert resp.status_code in (401, 403)
