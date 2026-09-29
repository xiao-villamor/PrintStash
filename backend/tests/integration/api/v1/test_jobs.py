"""The Jobs API and the live events stream.

Every route that accepts background work returns a ``job_id``; this is where the
user follows it, cancels it, or retries it. The API's promises are about who
sees what: a user sees only their own Jobs, another user's Job is a 404 rather
than a 403 (an id is not a way to learn that a Job exists), and scheduled
backups reach an administrator's Tasks without exposing other system Jobs.
Cancel withdraws the intent before
stopping the execution, so the reconciler cannot bring a cancelled import back;
retry is refused whenever the subject can no longer be worked.

The events socket carries notices, never data: a client refetches through these
routes. So the socket's own promise is authorization of each channel, checked
when it is joined.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.v1.jobs import _may_subscribe, events_ws
from app.core.time import utcnow
from app.db.models import (
    IngestRequest,
    IngestRequestKind,
    Job,
    JobKind,
    JobState,
    StagingLease,
    User,
)
from app.modules.identity import ws_tickets
from app.modules.identity.auth import create_access_token
from app.modules.work.catalog import get_engine
from app.runtime.realtime import InProcessBus
from tests.factories import build_collection, build_user, grant_collection_role


def _headers(user: User, scope: str = "write") -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner(db_session: Session) -> User:
    return build_user(db_session, "jobs-owner")


@pytest.fixture
def stranger(db_session: Session) -> User:
    return build_user(db_session, "jobs-stranger")


@pytest.fixture
def admin(db_session: Session) -> User:
    return build_user(db_session, "jobs-admin", superuser=True)


class TestListJobs:
    def test_lists_the_callers_own_jobs(
        self, client: TestClient, owner: User, stranger: User, make_job
    ) -> None:
        own = make_job(owner=owner, state=JobState.RUNNING)
        make_job(owner=stranger, state=JobState.RUNNING)

        response = client.get("/api/v1/jobs", headers=_headers(owner))

        assert response.status_code == 200, response.text
        assert [job["job_id"] for job in response.json()] == [own.id]
        assert response.headers["cache-control"] == "no-store"

    def test_never_serializes_the_owner(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        make_job(owner=owner)

        (job,) = client.get("/api/v1/jobs", headers=_headers(owner)).json()

        assert "owner_user_id" not in job

    def test_an_administrator_sees_every_users_jobs_but_not_system_jobs(
        self,
        client: TestClient,
        admin: User,
        owner: User,
        stranger: User,
        make_job,
    ) -> None:
        mine = make_job(owner=owner)
        theirs = make_job(owner=stranger)
        make_job(kind=JobKind.DERIVATIVES_MESH)

        response = client.get("/api/v1/jobs", headers=_headers(admin))

        assert {job["job_id"] for job in response.json()} == {mine.id, theirs.id}

    def test_an_administrator_asks_for_system_jobs(
        self, client: TestClient, admin: User, make_job
    ) -> None:
        system = make_job(kind=JobKind.DERIVATIVES_MESH)

        response = client.get(
            "/api/v1/jobs", headers=_headers(admin), params={"include_system": True}
        )

        assert [job["job_id"] for job in response.json()] == [system.id]

    def test_an_administrator_sees_scheduled_backup_progress_in_tasks(
        self, client: TestClient, admin: User, make_job
    ) -> None:
        automatic = make_job(
            kind=JobKind.BACKUPS_AUTOMATIC,
            status_json=json.dumps({"stage": "archiving", "processed": 2, "total": 5}),
        )

        response = client.get("/api/v1/jobs", headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert [job["job_id"] for job in response.json()] == [automatic.id]
        assert (response.json()[0]["stage"], response.json()[0]["processed"]) == (
            "archiving",
            2,
        )

    def test_a_regular_user_cannot_list_scheduled_backup_progress(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        make_job(kind=JobKind.BACKUPS_AUTOMATIC)

        response = client.get("/api/v1/jobs", headers=_headers(owner))

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_a_user_asking_for_system_jobs_is_refused(
        self, client: TestClient, owner: User
    ) -> None:
        response = client.get(
            "/api/v1/jobs", headers=_headers(owner), params={"include_system": True}
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "admin_required"

    def test_filters_by_definition(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        wanted = make_job(owner=owner, kind=JobKind.INGESTION_URL)
        make_job(owner=owner, kind=JobKind.BACKUPS_CREATE)

        response = client.get(
            "/api/v1/jobs",
            headers=_headers(owner),
            params={"kind": JobKind.INGESTION_URL},
        )

        assert [job["job_id"] for job in response.json()] == [wanted.id]

    def test_a_tracked_job_is_listed_beyond_the_terminal_tail(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        tracked = make_job(
            owner=owner,
            state=JobState.COMPLETED,
            updated_at=utcnow() - timedelta(hours=1),
        )
        make_job(owner=owner, state=JobState.COMPLETED)

        response = client.get(
            "/api/v1/jobs",
            headers=_headers(owner),
            params={"terminal_limit": 1, "tracked_job_id": [tracked.id, tracked.id]},
        )

        listed = [job["job_id"] for job in response.json()]
        assert listed.count(tracked.id) == 1
        assert len(listed) == 2

    def test_explicit_tracking_is_bounded(
        self, client: TestClient, owner: User
    ) -> None:
        response = client.get(
            "/api/v1/jobs",
            headers=_headers(owner),
            params={"tracked_job_id": [f"job-{index}" for index in range(51)]},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "too_many_tracked_job_ids"

    def test_the_terminal_tail_is_bounded(
        self, client: TestClient, owner: User
    ) -> None:
        response = client.get(
            "/api/v1/jobs", headers=_headers(owner), params={"terminal_limit": 101}
        )

        assert response.status_code == 422, response.text

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/jobs").status_code == 401


class TestGetJob:
    def test_returns_the_callers_job(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        job = make_job(
            owner=owner,
            state=JobState.RUNNING,
            status_json=json.dumps({"stage": "hashing", "progress": 40}),
        )

        response = client.get(f"/api/v1/jobs/{job.id}", headers=_headers(owner))

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["job_id"], body["state"], body["stage"], body["progress"]) == (
            job.id,
            "running",
            "hashing",
            40.0,
        )
        assert response.headers["cache-control"] == "no-store"

    def test_another_users_job_is_not_found(
        self, client: TestClient, owner: User, stranger: User, make_job
    ) -> None:
        job = make_job(owner=stranger)

        response = client.get(f"/api/v1/jobs/{job.id}", headers=_headers(owner))

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "job_not_found"

    def test_a_system_job_is_not_found_for_a_user(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        job = make_job(kind=JobKind.DERIVATIVES_MESH)

        response = client.get(f"/api/v1/jobs/{job.id}", headers=_headers(owner))

        assert response.status_code == 404, response.text

    def test_an_administrator_reads_any_job(
        self, client: TestClient, admin: User, stranger: User, make_job
    ) -> None:
        job = make_job(owner=stranger)

        response = client.get(f"/api/v1/jobs/{job.id}", headers=_headers(admin))

        assert response.status_code == 200, response.text

    def test_an_unknown_job_is_not_found(self, client: TestClient, owner: User) -> None:
        response = client.get("/api/v1/jobs/no-such-job", headers=_headers(owner))

        assert response.status_code == 404, response.text


class TestCancelJob:
    def test_withdraws_a_queued_import(
        self,
        client: TestClient,
        db_session: Session,
        owner: User,
        make_ingest_request,
    ) -> None:
        request = make_ingest_request(owner, source_credential="cookie=secret")

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["state"], body["error"]) == ("cancelled", "cancelled_by_user")
        db_session.expire_all()
        refreshed = db_session.get(IngestRequest, request.job_id)
        assert refreshed is not None
        assert refreshed.source_credential is None

    def test_releases_the_staged_bytes_of_a_cancelled_upload(
        self,
        client: TestClient,
        db_session: Session,
        owner: User,
        make_ingest_request,
        tmp_path,
    ) -> None:
        # Cancel means the upload is withdrawn now, not when its lease expires.
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        staged = tmp_path / "upload.stl"
        staged.write_bytes(b"solid x\nendsolid x\n")
        db_session.add(
            StagingLease(
                id="cancel-lease",
                path=str(staged),
                owner_user_id=owner.id,
                job_id=request.job_id,
                size_bytes=staged.stat().st_size,
                sha256="e" * 64,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        db_session.commit()

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        assert not staged.exists()
        db_session.expire_all()
        assert db_session.get(StagingLease, "cancel-lease") is None

    def test_stops_the_running_execution(
        self,
        client: TestClient,
        owner: User,
        make_ingest_request,
        db_session: Session,
        monkeypatch,
    ) -> None:
        request = make_ingest_request(owner, state=JobState.RUNNING)
        job = db_session.get(Job, request.job_id)
        assert job is not None
        job.attempts = 2
        db_session.add(job)
        db_session.commit()
        cancelled: list[str] = []
        monkeypatch.setattr(get_engine(), "cancel", cancelled.append)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        assert cancelled == [f"{request.job_id}:2"]

    def test_an_engine_that_cannot_cancel_does_not_fail_the_request(
        self,
        client: TestClient,
        owner: User,
        make_ingest_request,
        db_session: Session,
        monkeypatch,
    ) -> None:
        # The Job is already settled as cancelled; the engine converges when the
        # execution next checks in and finds its Job terminal.
        request = make_ingest_request(owner, state=JobState.RUNNING)
        job = db_session.get(Job, request.job_id)
        assert job is not None
        job.attempts = 1
        db_session.add(job)
        db_session.commit()

        def broken(_execution_id: str) -> None:
            raise RuntimeError("engine unreachable")

        monkeypatch.setattr(get_engine(), "cancel", broken)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "cancelled"

    def test_a_finished_job_cannot_be_cancelled(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, state=JobState.COMPLETED)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "job_not_active"

    def test_another_users_job_is_not_found(
        self, client: TestClient, owner: User, stranger: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(stranger)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel", headers=_headers(owner)
        )

        assert response.status_code == 404, response.text

    def test_a_read_only_token_cannot_cancel(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/cancel",
            headers=_headers(owner, scope="read"),
        )

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"


class TestRetryJob:
    def test_requeues_a_failed_import(
        self, client: TestClient, db_session: Session, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, state=JobState.FAILED)
        job = db_session.get(Job, request.job_id)
        assert job is not None
        job.resubmits = 3
        job.status_json = json.dumps(
            {"error": "download_failed", "total": 4, "failed": 4, "progress": 100}
        )
        db_session.add(job)
        db_session.commit()

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        # The outcome of the failed attempt is gone; what the user already
        # knows about the work (its size) stays.
        assert (body["state"], body["resubmits"], body["error"]) == ("queued", 0, None)
        assert (body["total"], body["failed"], body["progress"]) == (4, 0, None)
        assert body["finished_at"] is None

    def test_a_cancelled_job_can_be_retried(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, state=JobState.CANCELLED)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "queued"

    def test_a_completed_job_is_not_retryable(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, state=JobState.COMPLETED)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "job_not_retryable"

    def test_a_subject_with_an_active_job_is_busy(
        self, client: TestClient, owner: User, make_ingest_request, make_job
    ) -> None:
        request = make_ingest_request(owner, state=JobState.FAILED)
        make_job(
            kind=JobKind.INGESTION_URL,
            owner=owner,
            subject=f"ingest_request/{request.job_id}",
        )

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "job_subject_busy"

    def test_a_job_whose_intent_is_gone_cannot_be_retried(
        self, client: TestClient, owner: User, make_job
    ) -> None:
        job = make_job(
            kind=JobKind.INGESTION_URL,
            owner=owner,
            state=JobState.FAILED,
            subject="ingest_request/vanished",
        )

        response = client.post(f"/api/v1/jobs/{job.id}/retry", headers=_headers(owner))

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "job_subject_gone"

    def test_a_manifest_already_selected_from_cannot_be_retried(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        # Retrying would produce a second manifest for a selection that has
        # already imported its files.
        request = make_ingest_request(
            owner,
            state=JobState.FAILED,
            manifest_json=json.dumps({"kind": "archive", "claimed": True}),
        )

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 410, response.text

    def test_an_upload_whose_staged_bytes_are_gone_cannot_be_retried(
        self, client: TestClient, owner: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(
            owner, kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 410, response.text

    def test_another_users_job_is_not_found(
        self, client: TestClient, owner: User, stranger: User, make_ingest_request
    ) -> None:
        request = make_ingest_request(stranger, state=JobState.FAILED)

        response = client.post(
            f"/api/v1/jobs/{request.job_id}/retry", headers=_headers(owner)
        )

        assert response.status_code == 404, response.text


def _ticket(client: TestClient, user: User) -> str:
    response = client.post("/api/v1/events/ticket", headers=_headers(user))
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _wait_subscribed(bus, channel: str) -> None:
    deadline = time.monotonic() + 5
    while not bus._subscribers.get(channel):
        assert time.monotonic() < deadline, f"never subscribed to {channel}"
        time.sleep(0.01)


class TestEventsSocket:
    def test_a_ticket_opens_the_stream_with_a_resync(
        self, client: TestClient, owner: User
    ) -> None:
        response = client.post("/api/v1/events/ticket", headers=_headers(owner))

        assert response.json()["expires_in"] <= ws_tickets.TTL_SECONDS
        with client.websocket_connect(
            f"/api/v1/events/ws?ticket={response.json()['ticket']}"
        ) as ws:
            assert ws.receive_json() == {"type": "resync"}

    def test_delivers_notices_about_the_users_own_jobs(
        self, client: TestClient, app, owner: User
    ) -> None:
        bus = app.state.event_bus
        with client.websocket_connect(
            f"/api/v1/events/ws?ticket={_ticket(client, owner)}"
        ) as ws:
            ws.receive_json()
            notice = {"type": "job", "job_id": "j1", "state": "running"}
            ws.portal.call(bus.publish, f"jobs:{owner.id}", notice)

            assert ws.receive_json() == notice

    def test_follows_a_model_the_user_may_view(
        self, client: TestClient, app, db_session: Session, owner: User, make_model
    ) -> None:
        shelf = build_collection(db_session, "Shared shelf")
        grant_collection_role(db_session, owner, shelf)
        model = make_model(collection=shelf)
        bus = app.state.event_bus
        with client.websocket_connect(
            f"/api/v1/events/ws?ticket={_ticket(client, owner)}"
        ) as ws:
            ws.receive_json()
            ws.send_json({"subscribe": f"model:{model.id}"})
            # The acknowledgement tells the client to refetch what changed
            # before the subscription took effect.
            assert ws.receive_json() == {
                "type": "subscribed",
                "channel": f"model:{model.id}",
            }
            _wait_subscribed(bus, f"model:{model.id}")
            notice = {"type": "derivative", "model_id": model.id, "state": "ready"}
            ws.portal.call(bus.publish, f"model:{model.id}", notice)

            assert ws.receive_json() == notice

            ws.send_json({"unsubscribe": f"model:{model.id}"})
            deadline = time.monotonic() + 5
            while bus._subscribers.get(f"model:{model.id}"):
                assert time.monotonic() < deadline
                time.sleep(0.01)

    def test_a_refused_subscription_is_not_acknowledged(
        self, client: TestClient, app, db_session: Session, owner: User, make_model
    ) -> None:
        hidden = make_model()
        shelf = build_collection(db_session, "Shared shelf")
        grant_collection_role(db_session, owner, shelf)
        visible = make_model(collection=shelf)
        bus = app.state.event_bus
        with client.websocket_connect(
            f"/api/v1/events/ws?ticket={_ticket(client, owner)}"
        ) as ws:
            ws.receive_json()
            ws.send_json({"subscribe": f"model:{hidden.id}"})
            ws.send_json({"subscribe": f"model:{visible.id}"})

            # Requests are handled in order, so the refused one answered nothing.
            assert ws.receive_json() == {
                "type": "subscribed",
                "channel": f"model:{visible.id}",
            }
            assert not bus._subscribers.get(f"model:{hidden.id}")

    def test_leaves_every_channel_on_disconnect(
        self, client: TestClient, app, owner: User
    ) -> None:
        bus = app.state.event_bus
        with client.websocket_connect(
            f"/api/v1/events/ws?ticket={_ticket(client, owner)}"
        ) as ws:
            ws.receive_json()

        deadline = time.monotonic() + 5
        while bus._subscribers.get(f"jobs:{owner.id}"):
            assert time.monotonic() < deadline
            time.sleep(0.01)

    def test_a_notice_to_a_vanished_client_ends_the_stream_quietly(
        self, client: TestClient, owner: User
    ) -> None:
        # The socket can die while a notice is being written to it; the
        # stream must then end as a disconnect, not crash on its next read.
        ticket = _ticket(client, owner)
        bus = InProcessBus()
        channel = f"jobs:{owner.id}"

        async def scenario() -> None:
            inbound: asyncio.Queue[dict] = asyncio.Queue()
            inbound.put_nowait({"type": "websocket.connect"})
            sends = 0

            async def send(message: dict) -> None:
                nonlocal sends
                if message["type"] == "websocket.send":
                    sends += 1
                    if sends > 1:
                        raise OSError("connection reset")

            scope = {
                "type": "websocket",
                "path": "/api/v1/events/ws",
                "query_string": f"ticket={ticket}".encode(),
                "headers": [],
                "app": SimpleNamespace(state=SimpleNamespace(event_bus=bus)),
            }
            stream = asyncio.create_task(events_ws(WebSocket(scope, inbound.get, send)))
            while not bus._subscribers.get(channel):
                await asyncio.sleep(0.01)
            await bus.publish(channel, {"type": "job", "job_id": "j1"})
            inbound.put_nowait({"type": "websocket.receive", "text": "{}"})

            await asyncio.wait_for(stream, timeout=5)

        asyncio.run(scenario())

        assert not bus._subscribers.get(channel)

    def test_a_replayed_ticket_is_refused(
        self, client: TestClient, owner: User
    ) -> None:
        ticket = _ticket(client, owner)
        with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}"):
            pass

        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}"):
                pass

        assert closed.value.code == 1008

    def test_an_access_token_is_not_a_ticket(
        self, client: TestClient, owner: User
    ) -> None:
        # An access token in a URL lands in proxy logs; only a one-use ticket
        # is accepted there.
        token = _headers(owner)["Authorization"].split(" ", 1)[1]

        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/api/v1/events/ws?ticket={token}"):
                pass

        assert closed.value.code == 1008

    def test_a_deactivated_user_is_refused(
        self, client: TestClient, db_session: Session, owner: User
    ) -> None:
        ticket = _ticket(client, owner)
        owner.is_active = False
        db_session.add(owner)
        db_session.commit()

        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}"):
                pass

        assert closed.value.code == 1008

    def test_a_process_without_an_event_bus_closes_the_stream(
        self, client: TestClient, app, owner: User, monkeypatch
    ) -> None:
        ticket = _ticket(client, owner)
        monkeypatch.setattr(app.state, "event_bus", None)

        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}"):
                pass

        assert closed.value.code == 1011

    def test_a_client_probing_for_channels_is_disconnected(
        self, client: TestClient, app, owner: User, monkeypatch
    ) -> None:
        # Every request is an authorization query; a refused one must count,
        # or one socket can issue them without end.
        from app.api.v1 import jobs as jobs_routes

        checks: list[str] = []
        monkeypatch.setattr(
            jobs_routes,
            "_subscription_allowed",
            lambda _user, channel: checks.append(channel) or False,
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                f"/api/v1/events/ws?ticket={_ticket(client, owner)}"
            ) as ws:
                ws.receive_json()
                for model_id in range(jobs_routes._MAX_SUBSCRIPTION_REQUESTS + 5):
                    ws.send_json({"subscribe": f"model:{model_id}"})
                ws.receive_json()

        assert closed.value.code == 1008
        assert len(checks) == jobs_routes._MAX_SUBSCRIPTION_REQUESTS

    def test_a_read_only_token_cannot_issue_a_ticket(
        self, client: TestClient, owner: User
    ) -> None:
        response = client.post(
            "/api/v1/events/ticket", headers=_headers(owner, scope="read")
        )

        assert response.status_code == 401, response.text


class TestChannelAuthorization:
    def test_a_user_may_follow_their_own_jobs(
        self, db_session: Session, owner: User
    ) -> None:
        assert _may_subscribe(db_session, owner, f"jobs:{owner.id}") is True

    def test_a_user_may_not_follow_another_users_jobs(
        self, db_session: Session, owner: User, stranger: User
    ) -> None:
        assert _may_subscribe(db_session, owner, f"jobs:{stranger.id}") is False

    def test_only_an_administrator_follows_all_work(
        self, db_session: Session, owner: User, admin: User
    ) -> None:
        assert _may_subscribe(db_session, owner, "work:admin") is False
        assert _may_subscribe(db_session, admin, "work:admin") is True

    def test_a_user_may_not_follow_a_model_they_cannot_view(
        self, db_session: Session, owner: User, make_model
    ) -> None:
        model = make_model(collection=build_collection(db_session, "Private"))

        assert _may_subscribe(db_session, owner, f"model:{model.id}") is False

    def test_an_administrator_follows_any_model(
        self, db_session: Session, admin: User, make_model
    ) -> None:
        model = make_model()

        assert _may_subscribe(db_session, admin, f"model:{model.id}") is True

    def test_a_trashed_model_cannot_be_followed(
        self, db_session: Session, admin: User, make_model
    ) -> None:
        model = make_model(trashed=True)

        assert _may_subscribe(db_session, admin, f"model:{model.id}") is False

    @pytest.mark.parametrize(
        "channel", ["model:999999", "model:abc", "printer:1", "*", "jobs:"]
    )
    def test_refuses_channels_it_does_not_know(
        self, db_session: Session, admin: User, channel: str
    ) -> None:
        assert _may_subscribe(db_session, admin, channel) is False
