"""Tests for the print statistics aggregation endpoint."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Collection,
    FilamentProfile,
    File,
    FileType,
    Metadata,
    Model,
    PrintJob,
    PrintJobState,
)
from app.services import print_results


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
) -> PrintJob:
    """Seed a PrintJob the way a real write path would: cost/effective grams
    resolved and frozen at completion (mirrors printer_hub/manual-log/import)."""
    job = PrintJob(
        file_id=file_row.id,
        model_id=model.id,
        remote_filename=file_row.original_filename,
        state=state,
        filament_used_g=grams,
        actual_duration_s=duration_s,
        finished_at=utcnow() - timedelta(days=finished_days_ago),
    )
    if state == PrintJobState.COMPLETED:
        job.filament_g_effective, job.cost = print_results.resolve_completion_cost(
            db_session, job
        )
    db_session.add(job)
    db_session.commit()
    return job


def test_print_stats_empty(client: TestClient, auth_headers: dict[str, str]) -> None:
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
    db_session.add(FilamentProfile(name="PETG", material_type="PETG", cost_per_kg=20.0))
    db_session.commit()

    collection = Collection(name="Functional", slug="functional", path="functional")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    model = _model(db_session, slug="bracket", collection_id=collection.id)
    file_row = _file_with_material(db_session, model, sha="a", material_type="PETG")
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
    job = PrintJob(
        file_id=file_row.id,
        model_id=model.id,
        remote_filename="c.gcode",
        state=PrintJobState.COMPLETED,
        filament_used_g=None,
        actual_duration_s=None,
        finished_at=utcnow() - timedelta(days=1),
    )
    job.filament_g_effective, job.cost = print_results.resolve_completion_cost(
        db_session, job
    )
    db_session.add(job)
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


def _oracle_totals(db_session: Session, period: str) -> dict:
    """Reimplements the pre-denormalization aggregation (live profile
    matching, no persisted cost column) as a correctness oracle for the
    SQL-based rewrite in ``model_views.print_statistics``."""
    from sqlmodel import func, select

    from app.db.scopes import live
    from app.services import model_views as mv

    lookback_days = mv._STATS_PERIODS.get(period, mv._STATS_PERIODS["30d"])
    end_at = utcnow()
    start_at = (
        end_at - timedelta(days=lookback_days) if lookback_days is not None else None
    )
    anchor = func.coalesce(PrintJob.finished_at, PrintJob.created_at)

    query = (
        select(PrintJob, Metadata)
        .join(File, File.id == PrintJob.file_id)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .where(live(PrintJob), PrintJob.state == PrintJobState.COMPLETED)
    )
    if start_at is not None:
        query = query.where(anchor >= start_at)

    rows = db_session.exec(query).all()
    profiles = mv._load_filament_profiles(db_session)

    total_cost, has_cost = 0.0, False
    total_filament_g, has_filament = 0.0, False
    total_duration_s = 0
    for job, md in rows:
        if job.filament_used_g is not None:
            grams = job.filament_used_g
            cost = mv.filament_cost_for_grams(profiles, md, grams)
        elif md is not None:
            grams = md.filament_weight_g
            cost = mv.filament_cost_for_grams(profiles, md, grams)
            if cost is None:
                cost = md.filament_cost
        else:
            grams, cost = None, None

        if cost is not None:
            total_cost += cost
            has_cost = True
        if grams is not None:
            total_filament_g += grams
            has_filament = True
        if job.actual_duration_s is not None:
            total_duration_s += job.actual_duration_s

    return {
        "total_prints": len(rows),
        "total_cost": round(total_cost, 4) if has_cost else None,
        "total_filament_g": round(total_filament_g, 2) if has_filament else None,
        "total_print_time_s": total_duration_s,
    }


def test_print_statistics_matches_oracle(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    db_session.add_all(
        [
            FilamentProfile(name="PETG", material_type="PETG", cost_per_kg=20.0),
            FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=18.0),
        ]
    )
    db_session.commit()

    functional = Collection(name="Functional", slug="functional", path="functional")
    db_session.add(functional)
    db_session.commit()
    db_session.refresh(functional)

    bracket = _model(db_session, slug="bracket", collection_id=functional.id)
    bracket_file = _file_with_material(
        db_session, bracket, sha="a", material_type="PETG"
    )
    _job(
        db_session,
        bracket,
        bracket_file,
        grams=100.0,
        duration_s=3600,
        finished_days_ago=2,
    )
    _job(
        db_session,
        bracket,
        bracket_file,
        grams=150.0,
        duration_s=1800,
        finished_days_ago=45,
    )

    loose = _model(db_session, slug="loose")
    loose_file = _file_with_material(db_session, loose, sha="b", material_type="PLA")
    _job(
        db_session, loose, loose_file, grams=50.0, duration_s=900, finished_days_ago=200
    )
    _job(db_session, loose, loose_file, state=PrintJobState.FAILED, grams=999.0)

    for period in ("7d", "30d", "90d", "1y", "all"):
        expected = _oracle_totals(db_session, period)
        resp = client.get(
            f"/api/v1/models/stats/prints?period={period}", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_prints"] == expected["total_prints"], period
        assert body["total_cost"] == expected["total_cost"], period
        assert body["total_filament_g"] == expected["total_filament_g"], period
        assert body["total_print_time_s"] == expected["total_print_time_s"], period


def test_print_stats_period_windows_filter_correctly(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session, slug="windowed")
    file_row = _file_with_material(db_session, model, sha="w", material_type="PLA")
    _job(db_session, model, file_row, grams=10.0, finished_days_ago=5)
    _job(db_session, model, file_row, grams=10.0, finished_days_ago=45)
    _job(db_session, model, file_row, grams=10.0, finished_days_ago=200)
    _job(db_session, model, file_row, grams=10.0, finished_days_ago=800)

    counts = {}
    for period in ("7d", "30d", "90d", "1y", "all"):
        resp = client.get(
            f"/api/v1/models/stats/prints?period={period}", headers=auth_headers
        )
        counts[period] = resp.json()["total_prints"]

    assert counts["7d"] == 1
    assert counts["30d"] == 1
    assert counts["90d"] == 2
    assert counts["1y"] == 3
    assert counts["all"] == 4


def test_print_stats_manual_job_without_finished_at_uses_created_at(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    model = _model(db_session, slug="manual")
    file_row = _file_with_material(db_session, model, sha="m", material_type="PLA")
    job = PrintJob(
        file_id=file_row.id,
        model_id=model.id,
        remote_filename=file_row.original_filename,
        state=PrintJobState.COMPLETED,
        filament_used_g=20.0,
        finished_at=None,
    )
    job.filament_g_effective, job.cost = print_results.resolve_completion_cost(
        db_session, job
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get("/api/v1/models/stats/prints?period=7d", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total_prints"] == 1


def test_print_stats_totals_are_none_when_no_job_has_cost(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    # No FilamentProfile at all, and no slicer estimate — cost can't be
    # resolved for anyone, so totals must read as None, not 0.
    model = _model(db_session, slug="uncosted")
    file_row = _file_with_material(db_session, model, sha="u", material_type="EXOTIC")
    _job(db_session, model, file_row, grams=None, duration_s=600)

    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_prints"] == 1
    assert body["total_cost"] is None
    assert body["total_filament_g"] is None


def test_completed_job_persists_cost_via_manual_log(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    from sqlmodel import select

    db_session.add(FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=20.0))
    db_session.commit()
    model = _model(db_session, slug="manual-log")
    file_row = _file_with_material(db_session, model, sha="ml", material_type="PLA")
    md = db_session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).one()
    md.filament_weight_g = 100.0
    db_session.add(md)
    db_session.commit()

    resp = client.post(
        f"/api/v1/models/{model.id}/print-jobs",
        json={
            "file_id": file_row.id,
            "printer_name": "Bench printer",
            "state": "completed",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    job = db_session.exec(select(PrintJob).where(PrintJob.model_id == model.id)).one()
    # No measured filament_used_g on a manual log; falls back to the slicer
    # estimate, resolved and persisted at creation time by the endpoint itself.
    assert job.filament_g_effective == 100.0
    assert job.cost == 2.0


def test_editing_profile_price_does_not_change_historical_cost(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    profile = FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=20.0)
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    model = _model(db_session, slug="frozen-cost")
    file_row = _file_with_material(db_session, model, sha="fz", material_type="PLA")
    _job(db_session, model, file_row, grams=100.0)

    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    before = resp.json()["total_cost"]
    assert before == 2.0

    profile.cost_per_kg = 999.0
    db_session.add(profile)
    db_session.commit()

    resp = client.get("/api/v1/models/stats/prints?period=30d", headers=auth_headers)
    after = resp.json()["total_cost"]
    assert after == before == 2.0
