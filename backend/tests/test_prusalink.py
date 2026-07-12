from pathlib import Path

import httpx
import pytest

from app.services.prusalink import PrusaLinkClient, PrusaLinkError


def _client(handler, *, auth_mode="api_key") -> PrusaLinkClient:
    return PrusaLinkClient(
        "http://prusa.local",
        auth_mode=auth_mode,
        username="maker",
        password="secret",
        api_key="key-123",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_api_key_status_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key-123"
        if request.url.path == "/api/v1/status":
            return httpx.Response(
                200,
                json={
                    "printer": {
                        "state": "PRINTING",
                        "telemetry": {
                            "temp-bed": {"actual": 59.5, "target": 60},
                            "temp-nozzle": {"actual": 214, "target": 215},
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "state": "PRINTING",
                "file": {"name": "cube.gcode"},
                "progress": 25,
                "time_printing": 120,
                "time_remaining": 360,
            },
        )

    result = await _client(handler).query_status()
    status = result["result"]["status"]
    assert status["print_stats"] == {
        "state": "printing",
        "filename": "cube.gcode",
        "message": "",
        "print_duration": 120,
    }
    assert status["virtual_sdcard"]["progress"] == 0.25
    assert status["heater_bed"]["temperature"] == 59.5
    assert status["extruder"]["target"] == 215
    assert status["prusalink"]["job_id"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw_progress", "expected"), [(1, 0.01), (0.5, 0.005)])
async def test_low_progress_is_not_misread_as_complete(
    raw_progress: float, expected: float
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/status":
            return httpx.Response(200, json={"printer": {"state": "PRINTING"}})
        return httpx.Response(
            200,
            json={"id": 1, "state": "PRINTING", "progress": raw_progress},
        )

    result = await _client(handler).query_status()
    assert result["result"]["status"]["virtual_sdcard"]["progress"] == expected


@pytest.mark.asyncio
async def test_digest_auth_challenge_is_answered() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Digest realm="prusalink", nonce="abc", qop="auth", algorithm=MD5'
                },
            )
        return httpx.Response(200, json={"printer": {"state": "IDLE"}})

    result = await _client(handler, auth_mode="digest").info()
    assert result["result"]["provider"] == "prusalink"
    assert len(requests) == 2
    assert requests[1].headers["Authorization"].startswith("Digest ")


@pytest.mark.asyncio
async def test_file_operations_and_controls(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/files/local" and request.method == "GET":
            return httpx.Response(
                200, json={"files": [{"name": "cube.gcode", "size": 5}]}
            )
        if request.url.path == "/api/v1/job" and request.method == "GET":
            return httpx.Response(200, json={"id": 7, "state": "PAUSED"})
        return httpx.Response(204)

    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(handler)
    assert (await client.list_files())[0]["filename"] == "cube.gcode"
    await client.upload(source, "folder/cube.gcode")
    await client.start("folder/cube.gcode")
    await client.delete_file("folder/cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()
    assert ("PUT", "/api/v1/files/local/folder/cube.gcode") in seen
    assert ("POST", "/api/files/local/folder/cube.gcode") in seen
    assert ("PUT", "/api/v1/job/7/pause") in seen
    assert ("PUT", "/api/v1/job/7/resume") in seen
    assert ("DELETE", "/api/v1/job/7") in seen


@pytest.mark.asyncio
async def test_auth_failure_has_stable_code() -> None:
    client = _client(lambda request: httpx.Response(403))
    with pytest.raises(PrusaLinkError) as exc:
        await client.info()
    assert exc.value.code == "provider_authentication_failed"


@pytest.mark.asyncio
async def test_idle_printer_tolerates_missing_job() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/job":
            return httpx.Response(404)
        return httpx.Response(200, json={"printer": {"state": "IDLE"}})

    result = await _client(handler).query_status()
    assert result["result"]["status"]["print_stats"]["state"] == "standby"


@pytest.mark.asyncio
async def test_remote_path_traversal_rejected(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(lambda request: httpx.Response(204))
    with pytest.raises(PrusaLinkError) as exc:
        await client.upload(source, "../cube.gcode")
    assert exc.value.code == "provider_error"
