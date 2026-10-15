"""An administrator can choose and request each added local text model over HTTP."""

import pytest

from app.core.config import _overlay
from app.db.models import JobKind
from app.modules.work.jobs import jobs

pytestmark = pytest.mark.asyncio


class TestLocalTextModels:
    @pytest.mark.parametrize("key", ["all-minilm-l6-v2", "multilingual-e5-small"])
    async def test_queues_a_download_of_an_added_model(
        self, key, api, superuser_headers, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "embedding_cache_dir", tmp_path / "models")
        response = await api.put(
            "/api/v1/config/ai-search",
            headers=superuser_headers,
            json={
                "enabled": True,
                "local_models_enabled": True,
                "download_enabled": True,
            },
        )
        assert response.status_code == 200, response.text
        response = await api.get("/api/v1/inference/models", headers=superuser_headers)
        assert response.status_code == 200, response.text
        offered = {
            row["key"]: row
            for row in response.json()
            if row["modality"] == "text" and row["curated"]
        }
        assert offered.keys() == {
            "bge-small-en-v1.5",
            "all-minilm-l6-v2",
            "multilingual-e5-small",
        }
        chosen = offered[key]
        assert not chosen["installed"]

        response = await api.post(
            f"/api/v1/inference/models/{key}/download",
            headers=superuser_headers,
        )

        assert response.status_code == 202, response.text
        status = jobs.get(response.json()["job_id"])
        assert status is not None
        assert (status.kind, status.state) == (
            JobKind.INFERENCE_MODEL_DOWNLOAD,
            "queued",
        )
        assert status.result == {"model_id": chosen["id"]}
