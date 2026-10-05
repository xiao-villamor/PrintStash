"""Batch imports expose confirmed Artifacts before later network work finishes."""

import asyncio
import contextvars
import threading
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request, Response

from app.core.config import _overlay
from app.modules.ingestion import import_resolvers
from tests.e2e._jobs import completed_job, settle
from tests.factories.content import gcode
from tests.fakes.server import start_server


class TestBatchImportWindows:
    @pytest.mark.asyncio
    async def test_exposes_first_artifact_while_next_download_is_blocked(
        self, api, superuser_headers, fakes, monkeypatch
    ) -> None:
        # The existing fakes fixture enables loopback egress; the actual URL
        # resolver, host allowlist and pinned transports still execute.
        del fakes
        monkeypatch.setitem(_overlay, "ingestion_batch_max_files", 1)
        monkeypatch.setitem(_overlay, "ingestion_batch_max_mb", 1)
        first_body = gcode(marker="early-network-first")
        second_body = gcode(marker="early-network-second")
        second_started = threading.Event()
        release_second = threading.Event()
        target = FastAPI()

        @target.post("/graphql/")
        async def graphql(request: Request):
            payload = await request.json()
            if "getDownloadLink" in payload["query"]:
                return {
                    "data": {
                        "getDownloadLink": {
                            "ok": True,
                            "output": {
                                "files": [
                                    {"id": "first", "link": base_url + "/first.gcode"},
                                    {
                                        "id": "second",
                                        "link": base_url + "/second.gcode",
                                    },
                                ]
                            },
                        }
                    }
                }
            return {
                "data": {
                    "print": {
                        "id": "42",
                        "name": "Network batch",
                        "gcodes": [
                            {
                                "id": "first",
                                "name": "first.gcode",
                                "fileSize": len(first_body),
                            },
                            {
                                "id": "second",
                                "name": "second.gcode",
                                "fileSize": len(second_body),
                            },
                        ],
                        "stls": [],
                        "slas": [],
                        "otherFiles": [],
                    }
                }
            }

        @target.get("/first.gcode")
        def first_download():
            return Response(first_body, media_type="application/octet-stream")

        @target.get("/second.gcode")
        def second_download():
            second_started.set()
            if not release_second.wait(15):
                return Response(status_code=504)
            return Response(second_body, media_type="application/octet-stream")

        server = start_server(target)
        base_url = server.base_url
        monkeypatch.setattr(
            import_resolvers, "_PRINTABLES_GRAPHQL", base_url + "/graphql/"
        )
        monkeypatch.setattr(
            import_resolvers,
            "_PRINTABLES_ALLOWED_HOSTS",
            frozenset({urlsplit(base_url).hostname}),
        )
        worker = None
        worker_errors = []
        try:
            reviewed = await completed_job(
                api,
                await api.post(
                    "/api/v1/ingest/url",
                    headers=superuser_headers,
                    json={
                        "url": "https://www.printables.com/model/42-network-batch",
                        "review": True,
                    },
                ),
                superuser_headers,
            )
            assert reviewed["result"]["kind"] == "model_files_manifest"
            files_token = reviewed["result"]["files_token"]
            selected = await api.post(
                f"/api/v1/ingest/url/files/{files_token}/select",
                headers=superuser_headers,
                json={"file_ids": ["first", "second"]},
            )
            assert selected.status_code == 202, selected.text
            job_id = selected.json()["job_id"]
            execution_context = contextvars.copy_context()

            def run_work():
                try:
                    execution_context.run(settle)
                except BaseException as exc:
                    worker_errors.append(exc)

            worker = threading.Thread(target=run_work, daemon=True)
            worker.start()
            assert await asyncio.to_thread(second_started.wait, 10), (
                "second download never started"
            )
            assert worker.is_alive()
            progress = await api.get(
                f"/api/v1/jobs/{job_id}", headers=superuser_headers
            )
            assert progress.status_code == 200, progress.text
            assert progress.json()["processed"] == 1
            assert progress.json()["succeeded"] == 1
            assert progress.json()["total"] is None
            assert progress.json()["progress"] is None
            models = await api.get("/api/v1/models", headers=superuser_headers)
            assert models.status_code == 200, models.text
            assert len(models.json()) == 1
            first_model_id = models.json()[0]["id"]
            model = await api.get(
                f"/api/v1/models/{first_model_id}", headers=superuser_headers
            )
            assert model.status_code == 200, model.text
            assert len(model.json()["files"]) == 1
            first_id = model.json()["files"][0]["id"]
            downloaded = await api.get(
                f"/api/v1/files/{first_id}/download", headers=superuser_headers
            )
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == first_body
        finally:
            release_second.set()
            if worker is not None:
                await asyncio.to_thread(worker.join, 20)
            server.stop()
        assert worker is not None and not worker.is_alive()
        assert worker_errors == []
        final = await api.get(f"/api/v1/jobs/{job_id}", headers=superuser_headers)
        assert final.status_code == 200, final.text
        assert final.json()["state"] == "completed"
        assert final.json()["processed"] == 2
        assert final.json()["succeeded"] == 2
        models = await api.get("/api/v1/models", headers=superuser_headers)
        assert models.status_code == 200, models.text
        assert len(models.json()) == 2
