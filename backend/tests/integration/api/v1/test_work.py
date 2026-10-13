"""The Background work administration API.

An administrator sees every lane (its concurrency, and how much is queued and
running), every job definition with its counts and schedule, the executors
and whether each is alive, and the latest failures. They act through three
routes, all superuser-only: a lane's concurrency is changed at runtime and
persists in the application database (so it survives an engine reset); every
queued Job of a definition can be cancelled, which withdraws their intent; and
a derivative kind can be re-derived, filling gaps ("missing") or redoing every
Artifact ("all").
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    DerivativeGroupRegeneration,
    DerivativeKind,
    DerivativeRegeneration,
    DerivativeState,
    JobKind,
    JobState,
    LaneName,
    User,
    WorkLaneOverride,
)
from app.modules.identity.auth import create_access_token
from app.modules.work.contracts import PassSubmission
from tests.factories import build_user


def _headers(user: User, scope: str = "admin") -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(db_session: Session) -> User:
    return build_user(db_session, "work-admin", superuser=True)


@pytest.fixture
def member(db_session: Session) -> User:
    return build_user(db_session, "work-member")


def _nudged(engine) -> set[JobKind]:
    return {
        execution.submission.source
        for execution in engine.executions.values()
        if isinstance(execution.submission, PassSubmission)
    }


class TestOverview:
    def test_lists_every_lane_with_its_depth(
        self, client: TestClient, admin: User, make_job, work_engine
    ) -> None:
        job = make_job(kind=JobKind.INGESTION_UPLOAD)
        from app.modules.work.submission import submit

        submit(job.id)

        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        lanes = {lane["name"]: lane for lane in body["lanes"]}
        assert set(lanes) == set(work_engine.catalog.lanes)
        assert (
            lanes[LaneName.INGEST]["queued"],
            lanes[LaneName.INGEST]["running"],
        ) == (1, 0)
        assert lanes[LaneName.INGEST]["overridden"] is False

    def test_counts_each_definitions_jobs(
        self, client: TestClient, admin: User, make_job
    ) -> None:
        make_job(kind=JobKind.SOURCES_SCAN)
        make_job(kind=JobKind.SOURCES_SCAN, state=JobState.FAILED)
        make_job(kind=JobKind.SOURCES_SCAN, state=JobState.COMPLETED)

        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        scan = next(d for d in body["definitions"] if d["name"] == JobKind.SOURCES_SCAN)
        assert (scan["queued"], scan["failed"], scan["completed"]) == (1, 1, 1)
        assert scan["last_finished_at"] is not None

    def test_names_the_derivative_kinds_a_definition_produces(
        self, client: TestClient, admin: User
    ) -> None:
        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        mesh = next(
            d for d in body["definitions"] if d["name"] == JobKind.DERIVATIVES_MESH
        )
        assert set(mesh["derivative_kinds"]) == {"metadata", "thumbnail"}

    def test_lists_recent_failures_without_their_owners(
        self, client: TestClient, admin: User, member: User, make_job
    ) -> None:
        failed = make_job(
            kind=JobKind.INGESTION_URL, owner=member, state=JobState.FAILED
        )

        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        assert [job["job_id"] for job in body["failed_jobs"]] == [failed.id]
        assert "owner_user_id" not in body["failed_jobs"][0]

    def test_counts_failed_derivatives(
        self, client: TestClient, admin: User, make_model, make_file, make_derivative
    ) -> None:
        artifact = make_file(make_model(), filename="part.stl")
        make_derivative(
            artifact, DerivativeKind.THUMBNAIL, state=DerivativeState.FAILED
        )

        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        assert body["failed_derivatives"] == 1

    def test_reports_whether_each_executor_is_alive(
        self, client: TestClient, admin: User, make_work_executor
    ) -> None:
        alive = make_work_executor("alive-executor")
        dead = make_work_executor("dead-executor", stale=True)

        body = client.get("/api/v1/admin/work", headers=_headers(admin)).json()

        staleness = {row["executor_id"]: row["stale"] for row in body["executors"]}
        assert staleness == {alive.executor_id: False, dead.executor_id: True}

    def test_is_for_administrators_only(self, client: TestClient, member: User) -> None:
        response = client.get("/api/v1/admin/work", headers=_headers(member))

        assert response.status_code == 403, response.text

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/admin/work").status_code == 401


class TestUpdateLane:
    def test_changes_a_lanes_concurrency_at_runtime(
        self, client: TestClient, admin: User, db_session: Session, work_engine
    ) -> None:
        response = client.put(
            f"/api/v1/admin/work/lanes/{LaneName.INGEST}",
            headers=_headers(admin),
            json={"concurrency": 5},
        )

        assert response.status_code == 200, response.text
        lane = next(
            lane for lane in response.json()["lanes"] if lane["name"] == LaneName.INGEST
        )
        assert (lane["concurrency"], lane["overridden"]) == (5, True)
        assert work_engine.concurrency[LaneName.INGEST] == 5
        # Persisted where the engine's disposable state cannot lose it.
        row = db_session.get(WorkLaneOverride, LaneName.INGEST)
        assert row is not None
        assert (row.concurrency, row.updated_by) == (5, admin.id)

    def test_null_restores_the_configured_concurrency(
        self, client: TestClient, admin: User, db_session: Session, work_engine
    ) -> None:
        client.put(
            f"/api/v1/admin/work/lanes/{LaneName.INGEST}",
            headers=_headers(admin),
            json={"concurrency": 5},
        )

        response = client.put(
            f"/api/v1/admin/work/lanes/{LaneName.INGEST}",
            headers=_headers(admin),
            json={"concurrency": None},
        )

        lane = next(
            lane for lane in response.json()["lanes"] if lane["name"] == LaneName.INGEST
        )
        assert lane["overridden"] is False
        assert lane["concurrency"] == lane["default_concurrency"]
        db_session.expire_all()
        assert db_session.get(WorkLaneOverride, LaneName.INGEST) is None

    def test_an_unknown_lane_is_refused(self, client: TestClient, admin: User) -> None:
        response = client.put(
            "/api/v1/admin/work/lanes/nope",
            headers=_headers(admin),
            json={"concurrency": 2},
        )

        # Lanes are a closed set: an unknown one never reaches the service.
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("concurrency", [0, 65])
    def test_refuses_a_concurrency_outside_its_bounds(
        self, client: TestClient, admin: User, concurrency: int
    ) -> None:
        response = client.put(
            f"/api/v1/admin/work/lanes/{LaneName.INGEST}",
            headers=_headers(admin),
            json={"concurrency": concurrency},
        )

        assert response.status_code == 422, response.text

    def test_is_for_administrators_only(self, client: TestClient, member: User) -> None:
        response = client.put(
            f"/api/v1/admin/work/lanes/{LaneName.INGEST}",
            headers=_headers(member, scope="write"),
            json={"concurrency": 3},
        )

        assert response.status_code == 403, response.text


class TestCancelQueued:
    def test_withdraws_every_queued_job_of_a_definition(
        self, client: TestClient, admin: User, make_ingest_request, make_job
    ) -> None:
        queued = [make_ingest_request(admin) for _ in range(2)]
        running = make_ingest_request(admin, state=JobState.RUNNING)
        other = make_job(kind=JobKind.SOURCES_SCAN)

        response = client.post(
            "/api/v1/admin/work/cancel-queued",
            headers=_headers(admin),
            json={"definition": JobKind.INGESTION_URL},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"cancelled": 2}
        states = {
            request.job_id: client.get(
                f"/api/v1/jobs/{request.job_id}", headers=_headers(admin)
            ).json()["state"]
            for request in (*queued, running)
        }
        assert states == {
            queued[0].job_id: "cancelled",
            queued[1].job_id: "cancelled",
            running.job_id: "running",
        }
        assert (
            client.get(f"/api/v1/jobs/{other.id}", headers=_headers(admin)).json()[
                "state"
            ]
            == "queued"
        )

    def test_a_name_outside_the_kinds_is_refused(
        self, client: TestClient, admin: User
    ) -> None:
        response = client.post(
            "/api/v1/admin/work/cancel-queued",
            headers=_headers(admin),
            json={"definition": "no.such.definition"},
        )

        assert response.status_code == 422, response.text

    def test_a_definition_this_process_lacks_is_not_found(
        self, client: TestClient, admin: User, work_engine
    ) -> None:
        from app.modules.work import catalog as catalog_module
        from app.modules.work.catalog import WorkCatalog

        catalog_module.bind(work_engine, WorkCatalog())

        response = client.post(
            "/api/v1/admin/work/cancel-queued",
            headers=_headers(admin),
            json={"definition": JobKind.SOURCES_SCAN},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "job_definition_not_found"

    def test_is_for_administrators_only(self, client: TestClient, member: User) -> None:
        response = client.post(
            "/api/v1/admin/work/cancel-queued",
            headers=_headers(member, scope="write"),
            json={"definition": JobKind.INGESTION_URL},
        )

        assert response.status_code == 403, response.text


class TestRegenerateDerivatives:
    def test_missing_only_nudges_the_producers(
        self, client: TestClient, admin: User, db_session: Session, work_engine
    ) -> None:
        response = client.post(
            f"/api/v1/admin/work/derivatives/{DerivativeKind.THUMBNAIL}/regenerate",
            headers=_headers(admin),
            json={"mode": "missing"},
        )

        assert response.status_code == 202, response.text
        assert {JobKind.DERIVATIVES_MESH, JobKind.DERIVATIVES_GCODE} <= _nudged(
            work_engine
        )
        assert db_session.exec(select(DerivativeRegeneration)).all() == []
        assert db_session.exec(select(DerivativeGroupRegeneration)).all() == []

    def test_all_marks_every_output_of_the_kind_stale(
        self, client: TestClient, admin: User, db_session: Session, work_engine
    ) -> None:
        before = utcnow() - timedelta(seconds=1)

        response = client.post(
            f"/api/v1/admin/work/derivatives/{DerivativeKind.THUMBNAIL}/regenerate",
            headers=_headers(admin),
            json={"mode": "all"},
        )

        assert response.json() == {"kind": DerivativeKind.THUMBNAIL, "mode": "all"}
        from app.modules.derivatives.kinds import definitions_for_kind

        for definition in definitions_for_kind(DerivativeKind.THUMBNAIL):
            row = db_session.get(
                DerivativeGroupRegeneration, (definition, DerivativeKind.THUMBNAIL)
            )
            assert row is not None
            assert row.requested_by == admin.id
            assert row.requested_at.replace(tzinfo=None) >= before.replace(tzinfo=None)
        assert JobKind.DERIVATIVES_MESH in _nudged(work_engine)

    def test_an_unknown_kind_is_refused(self, client: TestClient, admin: User) -> None:
        response = client.post(
            "/api/v1/admin/work/derivatives/hologram/regenerate",
            headers=_headers(admin),
            json={"mode": "all"},
        )

        # Derivative kinds are a closed set, refused before the service runs.
        assert response.status_code == 422, response.text

    def test_is_for_administrators_only(self, client: TestClient, member: User) -> None:
        response = client.post(
            f"/api/v1/admin/work/derivatives/{DerivativeKind.THUMBNAIL}/regenerate",
            headers=_headers(member, scope="write"),
            json={"mode": "all"},
        )

        assert response.status_code == 403, response.text
