"""Settings sample collection uses the same ingest and derivative path as an upload."""

import pytest


@pytest.mark.asyncio
async def test_add_samples_through_settings(api, superuser_headers, work_engine):
    response = await api.post(
        "/api/v1/config/sample-collection", headers=superuser_headers
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    repeated = await api.post(
        "/api/v1/config/sample-collection", headers=superuser_headers
    )
    assert repeated.json() == {"job_id": job_id}

    work_engine.drain()
    job = await api.get(f"/api/v1/jobs/{job_id}", headers=superuser_headers)
    assert job.json()["state"] == "completed", job.text

    browse = await api.get(
        "/api/v1/models/browse",
        headers=superuser_headers,
        params={"collection": "printstash-samples", "direct": "true"},
    )
    assert browse.status_code == 200, browse.text
    models = [item["model"] for item in browse.json()["items"]]
    assert {model["name"] for model in models} == {
        "PrintStash logo plaque",
        "20 mm calibration cube",
        "Small parts tray",
    }
    assert all(model["mesh_file_id"] is not None for model in models)
    after_completion = await api.post(
        "/api/v1/config/sample-collection", headers=superuser_headers
    )
    assert after_completion.json() == {"job_id": None}


@pytest.mark.asyncio
async def test_settings_readds_samples_after_collection_deletion(
    api, superuser_headers, work_engine
):
    endpoint = "/api/v1/config/sample-collection"
    first = await api.post(endpoint, headers=superuser_headers)
    old_job_id = first.json()["job_id"]
    work_engine.drain()

    lookup = await api.get(
        "/api/v1/collections/lookup",
        headers=superuser_headers,
        params={"path": "printstash-samples"},
    )
    collection_id = lookup.json()["collection"]["id"]
    removed = await api.delete(
        f"/api/v1/collections/{collection_id}",
        headers=superuser_headers,
        params={"recursive": "true"},
    )
    assert removed.status_code == 204, removed.text
    second = await api.post(endpoint, headers=superuser_headers)
    new_job_id = second.json()["job_id"]
    assert new_job_id and new_job_id != old_job_id
    work_engine.drain()

    browse = await api.get(
        "/api/v1/models/browse",
        headers=superuser_headers,
        params={"collection": "printstash-samples", "direct": "true"},
    )
    assert browse.json()["total"] == 3
