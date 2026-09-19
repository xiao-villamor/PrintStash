"""A configured server backfills and switches indexes through the real worker and HTTP API."""

import asyncio
import os
import subprocess
import sys
from contextlib import suppress
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import IndexGeneration, PassageVector
from app.db.session import _set_sqlite_pragmas, get_session_factory
from app.modules.inference.query import close_queries
from app.modules.inference.transport import close_client
from app.runtime.jobs import JobRegistry
from app.runtime.search import run_search
from tests.fakes.inference import InferenceFake
from tests.fakes.server import start_server
from tests.paths import BACKEND_DIR


@pytest.fixture
def restartable_indexer(e2e_db):
    processes = []
    database_url = e2e_db.get_bind().url.render_as_string(hide_password=False)

    def start(generation_id, *, collect_coverage=True):
        env = {
            **os.environ,
            "VAULT_DB_URL": database_url,
            "VAULT_SECRETS_KEY": "printstash-e2e-secrets-key",
        }
        if not collect_coverage:
            # This process is deliberately killed below to prove crash recovery.
            # An instrumented process cannot flush its SQLite coverage shard after
            # SIGKILL, leaving corrupt input for the suite-wide coverage combine.
            env.pop("COVERAGE_PROCESS_CONFIG", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.search_worker",
                "--generation",
                str(generation_id),
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(process)
        return process

    try:
        yield start
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.fixture
def wait_for_embedding_call():
    async def wait(fake, count, process):
        for _ in range(200):
            if len(fake.calls) >= count:
                return
            if process.poll() is not None:
                raise AssertionError(process.communicate()[0])
            await asyncio.sleep(0.05)
        raise AssertionError("indexer never reached the interrupted batch")

    return wait


@pytest_asyncio.fixture
async def indexing_server(api, superuser_headers, e2e_db):
    # Deployment configuration: optional native acceleration is installed and
    # explicitly enabled. No network/inference/worker implementation is replaced.
    previous = _overlay.get("search_native_vectors_enabled", False)
    _overlay["search_native_vectors_enabled"] = True
    # The E2E engine is created independently of the production engine. Every
    # connection, including one opened while the worker yields to HTTP writes,
    # must receive the same installed-extension and SQLite configuration hook.
    engine = e2e_db.get_bind()
    event.listen(engine, "connect", _set_sqlite_pragmas)
    fake = InferenceFake()
    server = start_server(fake.app())
    worker = asyncio.create_task(run_search())
    try:
        response = await api.post(
            "/api/v1/config/ai-search/endpoints",
            headers=superuser_headers,
            json={
                "base_url": server.base_url + "/v1",
                "model": fake.model,
                "native_dimension": 4,
            },
        )
        assert response.status_code == 201, response.text
        endpoint = response.json()
        response = await api.put(
            "/api/v1/config/ai-search",
            headers=superuser_headers,
            json={"enabled": True},
        )
        assert response.status_code == 200, response.text
        response = await api.post(
            "/api/v1/documents",
            headers=superuser_headers,
            json={"name": "Assembly guide", "body": "Fit the lid"},
        )
        assert response.status_code == 201, response.text
        yield fake, endpoint
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        close_queries()
        close_client()
        server.stop()
        event.remove(engine, "connect", _set_sqlite_pragmas)
        _overlay["search_native_vectors_enabled"] = previous


@pytest.fixture
def wait_for_active(api, superuser_headers):
    async def wait(generation_id):
        for _ in range(160):
            response = await api.get(
                "/api/v1/config/ai-search/generations", headers=superuser_headers
            )
            assert response.status_code == 200, response.text
            generation = next(
                row for row in response.json() if row["id"] == generation_id
            )
            if generation["state"] == "active":
                return generation
            await asyncio.sleep(0.25)
        raise AssertionError(f"generation did not become active: {generation}")

    return wait


class TestSearchGenerationLifecycle:
    @pytest.mark.asyncio
    async def test_restores_native_search_without_the_extension(
        self, api, superuser_headers, indexing_server, wait_for_active, e2e_db
    ):
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        fake, endpoint = indexing_server
        proposal = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={"endpoint_id": endpoint["id"], "index_backend": "sqlite_vec"},
        )
        assert proposal.status_code == 202, proposal.text
        active = await wait_for_active(proposal.json()["id"])
        assert active["index_state"] == "ready", active
        vector = e2e_db.exec(select(PassageVector)).one()
        vector_id, original = vector.id, vector.vector_blob
        subject_id = vector.subject_id
        e2e_db.rollback()
        created = await api.post("/api/v1/backups", headers=superuser_headers)
        assert created.status_code == 202, created.text
        deleted = await api.delete(
            f"/api/v1/documents/{subject_id}", headers=superuser_headers
        )
        assert deleted.status_code == 204, deleted.text
        _overlay["search_native_vectors_enabled"] = False
        e2e_db.close()
        close_queries()
        restored = await api.post(
            f"/api/v1/backups/{created.json()['backup_id']}/restore",
            headers=superuser_headers,
        )
        assert restored.status_code == 200, restored.text
        with get_session_factory().scoped_session() as session:
            assert session.get(PassageVector, vector_id).vector_blob == original
            with pytest.raises(OperationalError, match="no such function"):
                session.execute(text("SELECT vec_version()"))
            session.rollback()
        result = await api.get(
            "/api/v1/search", params={"q": "Assembly"}, headers=superuser_headers
        )
        assert result.status_code == 200, result.text
        assert [
            (item["subject_type"], item["subject_id"])
            for item in result.json()["items"]
        ] == [("document", subject_id)]
        assert result.json()["semantic_ready"] is True
        assert set(result.json()["legs"]) == {"lexical", "semantic_text"}
        assert fake.calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("quantization", ["int8", "binary", "model"])
    async def test_serves_continuous_readers_during_a_transform_switch(
        self, api, superuser_headers, indexing_server, wait_for_active, quantization
    ):
        fake, endpoint = indexing_server
        proposal = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={"endpoint_id": endpoint["id"], "index_backend": "numpy"},
        )
        assert proposal.status_code == 202, proposal.text
        first = await wait_for_active(proposal.json()["id"])
        requests_before = len(fake.calls)
        completed = asyncio.Event()
        observations = []

        async def read():
            while not completed.is_set():
                response = await api.get(
                    "/api/v1/search",
                    headers=superuser_headers,
                    params={"q": "construction manual"},
                )
                observations.append((response.status_code, response.json()))
                await asyncio.sleep(0.02)

        reader = asyncio.create_task(read())
        other_server = None
        try:
            replacement_endpoint = endpoint["id"]
            if quantization == "model":
                other = InferenceFake(model="replacement-encoder", dimension=8)
                other_server = start_server(other.app())
                configured = await api.post(
                    "/api/v1/config/ai-search/endpoints",
                    headers=superuser_headers,
                    json={
                        "base_url": other_server.base_url + "/v1",
                        "model": other.model,
                        "native_dimension": 8,
                    },
                )
                assert configured.status_code == 201, configured.text
                replacement_endpoint = configured.json()["id"]
            replacement = await api.post(
                "/api/v1/config/ai-search/generations",
                headers=superuser_headers,
                json={
                    "endpoint_id": replacement_endpoint,
                    "index_backend": "sqlite_vec",
                    "quantization": "float32"
                    if quantization == "model"
                    else quantization,
                },
            )
            assert replacement.status_code == 202, replacement.text
            active = await wait_for_active(replacement.json()["id"])
            final = await api.get(
                "/api/v1/search",
                headers=superuser_headers,
                params={"q": "construction manual"},
            )
            observations.append((final.status_code, final.json()))
            if quantization == "model":
                assert any(
                    call["body"]["input"]
                    == ["Title: Assembly guide\nBody: Fit the lid"]
                    for call in other.calls
                )
                assert any(
                    call["body"]["input"] == ["construction manual"]
                    for call in other.calls
                )
        finally:
            completed.set()
            await reader
            if other_server is not None:
                other_server.stop()

        assert len(observations) >= 2
        assert {
            generation for _, body in observations for generation in body["generations"]
        } == {first["id"], active["id"]}
        assert all(
            status == 200
            and body["semantic_ready"]
            and [item["name"] for item in body["items"]] == ["Assembly guide"]
            for status, body in observations
        ), observations
        assert all(
            call["body"]["input"] == ["construction manual"]
            for call in fake.calls[requests_before:]
        )

    @pytest.mark.asyncio
    async def test_serves_hybrid_queries_through_http(
        self, api, superuser_headers, indexing_server, wait_for_active
    ):
        fake, endpoint = indexing_server
        proposal = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={"endpoint_id": endpoint["id"], "index_backend": "numpy"},
        )
        assert proposal.status_code == 202, proposal.text
        await wait_for_active(proposal.json()["id"])
        requests_before = len(fake.calls)

        response = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "how to put the parts together"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["semantic_ready"]
        assert [item["name"] for item in response.json()["items"]] == ["Assembly guide"]
        assert "semantic_text" in {
            evidence["leg"] for evidence in response.json()["items"][0]["evidence"]
        }
        assert len(fake.calls) == requests_before + 1
        cached = await api.get(
            "/api/v1/search",
            headers=superuser_headers,
            params={"q": "how to put the parts together"},
        )
        assert cached.json() == response.json()
        assert len(fake.calls) == requests_before + 1

    @pytest.mark.asyncio
    async def test_resumes_committed_work_after_process_loss(
        self, api, superuser_headers, restartable_indexer, wait_for_embedding_call
    ):
        fake = InferenceFake(hold_embedding_call=3)
        server = start_server(fake.app())
        try:
            endpoint = await api.post(
                "/api/v1/config/ai-search/endpoints",
                headers=superuser_headers,
                json={
                    "base_url": server.base_url + "/v1",
                    "model": fake.model,
                    "native_dimension": 4,
                },
            )
            assert endpoint.status_code == 201, endpoint.text
            enabled = await api.put(
                "/api/v1/config/ai-search",
                headers=superuser_headers,
                json={"enabled": True},
            )
            assert enabled.status_code == 200, enabled.text
            documents = await asyncio.gather(
                *(
                    api.post(
                        "/api/v1/documents",
                        headers=superuser_headers,
                        json={"name": f"Assembly step {index}", "body": "Fit the lid"},
                    )
                    for index in range(9)
                )
            )
            assert [response.status_code for response in documents] == [201] * 9
            from tests.search_projection import drain_search

            with get_session_factory().scoped_session() as session:
                drain_search(session)
            proposal = await api.post(
                "/api/v1/config/ai-search/generations",
                headers=superuser_headers,
                json={"endpoint_id": endpoint.json()["id"], "index_backend": "numpy"},
            )
            assert proposal.status_code == 202, proposal.text
            generation_id = proposal.json()["id"]
            process = restartable_indexer(generation_id, collect_coverage=False)
            await wait_for_embedding_call(fake, 3, process)
            process.kill()
            await asyncio.to_thread(process.wait, 10)
            with get_session_factory().scoped_session() as session:
                committed_ids = session.exec(select(PassageVector.passage_id)).all()
                assert len(committed_ids) == 8
                generation = session.get(IndexGeneration, generation_id)
                assert generation.lease_token
                # Advance the persisted lease to its expiry instead of adding a
                # three-minute wall-clock delay to every test-suite run.
                generation.lease_expires_at = utcnow() - timedelta(seconds=1)
                session.add(generation)
                session.commit()
            fake.hold_embedding_call = None

            restarted = restartable_indexer(generation_id)
            output, _ = await asyncio.to_thread(restarted.communicate, timeout=30)

            assert restarted.returncode == 0, output
            status = await api.get(
                "/api/v1/config/ai-search/generations", headers=superuser_headers
            )
            assert (status.json()[0]["state"], status.json()[0]["indexed"]) == (
                "active",
                9,
            )
            with get_session_factory().scoped_session() as session:
                assert set(committed_ids) < set(
                    session.exec(select(PassageVector.passage_id)).all()
                )
            assert (
                len([call for call in fake.calls if len(call["body"]["input"]) == 8])
                == 1
            )
            assert JobRegistry().get(proposal.json()["job_id"]).state == "completed"
        finally:
            fake.hold_embedding_call = None
            close_client()
            server.stop()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("quantization", ["float32", "int8", "binary"])
    async def test_switches_to_native_index_without_reembedding(
        self, api, superuser_headers, indexing_server, wait_for_active, quantization
    ):
        fake, endpoint = indexing_server
        first = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={"endpoint_id": endpoint["id"], "index_backend": "numpy"},
        )
        assert first.status_code == 202, first.text
        first_active = await wait_for_active(first.json()["id"])
        assert first_active["indexed"] == first_active["eligible"] == 1
        requests_before = len(fake.calls)

        replacement = await api.post(
            "/api/v1/config/ai-search/generations",
            headers=superuser_headers,
            json={
                "endpoint_id": endpoint["id"],
                "index_backend": "sqlite_vec",
                "quantization": quantization,
            },
        )
        assert replacement.status_code == 202, replacement.text
        active = await wait_for_active(replacement.json()["id"])

        assert (active["index_backend"], active["index_state"], active["copied"]) == (
            "sqlite_vec",
            "ready",
            1,
        )
        assert active["index_error"] is None
        assert len(fake.calls) == requests_before
        listed = await api.get(
            "/api/v1/config/ai-search/generations", headers=superuser_headers
        )
        assert [(row["id"], row["state"]) for row in listed.json()] == [
            (active["id"], "active"),
            (first_active["id"], "retired"),
        ]
