"""Resin source uploads retain printer bytes while publishing usable derivatives."""

import pytest

from tests.e2e._jobs import completed_job
from tests.paths import CORE_PACKAGE_ROOT


class TestResinUpload:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("extension", ["ctb", "goo"])
    async def test_ingests_resin_source(self, api, tmp_path, extension):
        from app.modules.storage.storage_backend.runtime import init_backend

        setup = await api.post(
            "/api/v1/setup",
            json={
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(tmp_path / "files"),
                "thumb_dir": str(tmp_path / "thumbs"),
            },
        )
        assert setup.status_code == 201, setup.text
        init_backend()
        headers = {"Authorization": f"Bearer {setup.json()['access_token']}"}
        payload = (
            CORE_PACKAGE_ROOT / "tests/fixtures/resin" / f"header.{extension}"
        ).read_bytes()
        uploaded = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": (f"slice.{extension}", payload, "application/octet-stream")},
            data={"model_name": "Resin slice"},
        )
        job = await completed_job(api, uploaded, headers)
        model = await api.get(f"/api/v1/models/{job['model_id']}", headers=headers)
        assert model.status_code == 200, model.text
        artifact = model.json()["files"][0]
        assert artifact["file_type"] == "resin"
        assert artifact["metadata"]["layer_count"] == 120
        assert artifact["metadata"]["resin_volume_ml"] == 2.5
        assert artifact["metadata"]["exposure_time_s"] == 2.5
        assert artifact["metadata"]["estimated_time_s"] == 600
        assert artifact["metadata"]["layer_height_mm"] == pytest.approx(0.05)
        image = await api.get(
            f"/api/v1/files/{job['file_id']}/thumbnail", headers=headers
        )
        assert image.status_code == 200, image.text
        assert image.headers["content-type"] == "image/webp"
        assert image.content[:4] == b"RIFF"
        download = await api.get(
            f"/api/v1/files/{job['file_id']}/download", headers=headers
        )
        assert download.status_code == 200
        assert download.content == payload
        derivatives = (
            await api.get(
                f"/api/v1/files/{job['file_id']}/derivatives", headers=headers
            )
        ).json()
        assert {d["kind"]: d["state"] for d in derivatives} == {
            "metadata": "ready",
            "thumbnail": "ready",
        }
        # Re-ingesting the same bytes follows the ordinary content deduplication contract.
        duplicate = await completed_job(
            api,
            await api.post(
                "/api/v1/ingest/model",
                headers=headers,
                files={
                    "file": (f"copy.{extension}", payload, "application/octet-stream")
                },
            ),
            headers,
        )
        assert duplicate["model_id"] == job["model_id"]
