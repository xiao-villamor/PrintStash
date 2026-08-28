"""Aggregating print history into a window of totals.

This endpoint answers "what did I actually spend last month" — filament, money, machine
time — and every number in it is a sum over the jobs that *finished*, so both of its
failure modes are silent. A job counted in the wrong window makes a month look busier than
it was; a cost recomputed from today's filament price rewrites history every time somebody
edits a profile. Each produces a plausible number, which is why they need tests rather
than eyes.

So a cost is stamped on the job when it completes and never recalculated, and a window
that finds nothing reports zeros with `None` totals rather than an error — an empty month
is a real answer, not a failure.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    FilamentProfile,
    File,
    FileType,
    Metadata,
    Model,
    PrintJob,
    PrintJobState,
)
from app.services import print_results
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    print_job_config,
)


def _model(
    db_session: Session, *, slug: str, collection_id: int | None = None
) -> Model:
    model = build_model(
        db_session,
        name=slug.title(),
        slug=slug,
        hash=f"{slug:0<64}"[:64],
        collection_id=collection_id,
    )
    return model


def _file_with_material(
    db_session: Session,
    model: Model,
    *,
    sha: str,
    material_type: str,
    material_brand: str | None = None,
) -> File:
    file_row = build_file(
        db_session,
        model,
        path=f"/data/files/{model.slug}/{sha}.gcode",
        filename=f"{sha}.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=123,
        sha256=sha * 64,
    )
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
    job = print_job_config(
        file_row,
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


class TestPrintStats:
    def test_reports_zeros_for_a_window_with_no_prints(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"] == "30d"
        assert body["total_prints"] == 0
        assert body["total_cost"] is None
        assert body["top_collections"] == []
        assert body["top_filaments"] == []

    def test_sums_the_jobs_that_completed(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        db_session.add(
            FilamentProfile(name="PETG", material_type="PETG", cost_per_kg=20.0)
        )
        db_session.commit()

        collection = build_collection(
            db_session, name="Functional", slug="functional", path="functional"
        )

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

    def test_groups_models_with_no_collection_together(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _model(db_session, slug="loose")
        file_row = _file_with_material(db_session, model, sha="b", material_type="PLA")
        _job(db_session, model, file_row, grams=50.0)

        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["top_collections"][0]["name"] == "Uncategorized"
        assert body["top_collections"][0]["collection_id"] is None

    def test_falls_back_to_the_slicer_estimate_when_a_job_recorded_no_filament(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        # A Bambu/manual job with no measured filament — stats must use the slicer
        # estimate stored on Metadata instead of reading as "—".
        model = _model(db_session, slug="estimated")
        file_row = build_file(
            db_session,
            model,
            path=f"/data/files/{model.slug}/c.gcode",
            filename="c.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=123,
            sha256="c" * 64,
        )
        db_session.add(
            Metadata(
                file_id=file_row.id,
                material_type="PLA",
                filament_weight_g=75.0,
                filament_cost=1.5,
                estimated_time_s=1200,
            )
        )
        job = print_job_config(
            file_row,
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

        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_filament_g"] == 75.0
        assert body["avg_filament_g"] == 75.0
        assert body["total_cost"] == 1.5
        assert body["total_print_time_s"] == 1200

    def test_matches_a_hand_computed_total(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        db_session.add_all(
            [
                FilamentProfile(name="PETG", material_type="PETG", cost_per_kg=20.0),
                FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=18.0),
            ]
        )
        db_session.commit()

        functional = build_collection(
            db_session, name="Functional", slug="functional", path="functional"
        )

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
        loose_file = _file_with_material(
            db_session, loose, sha="b", material_type="PLA"
        )
        _job(
            db_session,
            loose,
            loose_file,
            grams=50.0,
            duration_s=900,
            finished_days_ago=200,
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

    def test_counts_only_the_jobs_inside_the_requested_window(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
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

    def test_dates_a_manual_job_with_no_finish_time_by_when_it_was_logged(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = _model(db_session, slug="manual")
        file_row = _file_with_material(db_session, model, sha="m", material_type="PLA")
        job = print_job_config(
            file_row,
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

    def test_reports_no_total_rather_than_zero_when_no_job_has_a_cost(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        # No FilamentProfile at all, and no slicer estimate — cost can't be
        # resolved for anyone, so totals must read as None, not 0.
        model = _model(db_session, slug="uncosted")
        file_row = _file_with_material(
            db_session, model, sha="u", material_type="EXOTIC"
        )
        _job(db_session, model, file_row, grams=None, duration_s=600)

        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_prints"] == 1
        assert body["total_cost"] is None
        assert body["total_filament_g"] is None

    def test_stamps_the_cost_on_a_job_when_it_completes(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        from sqlmodel import select

        db_session.add(
            FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=20.0)
        )
        db_session.commit()
        model = _model(db_session, slug="manual-log")
        file_row = _file_with_material(db_session, model, sha="ml", material_type="PLA")
        md = db_session.exec(
            select(Metadata).where(Metadata.file_id == file_row.id)
        ).one()
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

        job = db_session.exec(
            select(PrintJob).where(PrintJob.model_id == model.id)
        ).one()
        # No measured filament_used_g on a manual log; falls back to the slicer
        # estimate, resolved and persisted at creation time by the endpoint itself.
        assert job.filament_g_effective == 100.0
        assert job.cost == 2.0

    def test_leaves_a_recorded_cost_alone_when_the_filament_price_changes(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        profile = FilamentProfile(name="PLA", material_type="PLA", cost_per_kg=20.0)
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        model = _model(db_session, slug="frozen-cost")
        file_row = _file_with_material(db_session, model, sha="fz", material_type="PLA")
        _job(db_session, model, file_row, grams=100.0)

        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        before = resp.json()["total_cost"]
        assert before == 2.0

        profile.cost_per_kg = 999.0
        db_session.add(profile)
        db_session.commit()

        resp = client.get(
            "/api/v1/models/stats/prints?period=30d", headers=auth_headers
        )
        after = resp.json()["total_cost"]
        assert after == before == 2.0

    def test_rejects_a_non_superuser(self, client: TestClient, user_headers) -> None:
        response = client.get(
            "/api/v1/models/stats/prints", headers=user_headers("stats-ordinary")
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/stats/prints").status_code == 401
