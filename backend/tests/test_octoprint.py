from pathlib import Path

import httpx
import pytest

from app.services.octoprint import OctoPrintClient, OctoPrintError


def _client(handler) -> OctoPrintClient:
    return OctoPrintClient(
        "http://octopi.local",
        api_key="key-123",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_printing_status_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key-123"
        if request.url.path == "/api/printer":
            return httpx.Response(
                200,
                json={
                    "state": {"text": "Printing", "flags": {"printing": True}},
                    "temperature": {
                        "bed": {"actual": 59.5, "target": 60},
                        "tool0": {"actual": 214, "target": 215},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "job": {"file": {"name": "cube.gcode"}},
                "progress": {"completion": 25.0, "printTime": 120},
                "state": "Printing",
            },
        )

    result = await _client(handler).query_status()
    status = result["result"]["status"]
    assert status["print_stats"]["state"] == "printing"
    assert status["print_stats"]["filename"] == "cube.gcode"
    assert status["print_stats"]["print_duration"] == 120
    assert status["virtual_sdcard"]["progress"] == 0.25
    assert status["heater_bed"]["temperature"] == 59.5
    assert status["extruder"]["target"] == 215


@pytest.mark.asyncio
async def test_completion_at_100_with_no_active_flags_is_complete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(
                200, json={"state": {"text": "Operational", "flags": {}}}
            )
        return httpx.Response(
            200,
            json={
                "job": {"file": {"name": "cube.gcode"}},
                "progress": {"completion": 100.0},
            },
        )

    result = await _client(handler).query_status()
    assert result["result"]["status"]["print_stats"]["state"] == "complete"


@pytest.mark.asyncio
async def test_idle_printer_has_no_file_and_is_standby() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(
                200, json={"state": {"text": "Operational", "flags": {}}}
            )
        return httpx.Response(200, json={"job": {"file": {}}, "progress": {}})

    result = await _client(handler).query_status()
    assert result["result"]["status"]["print_stats"]["state"] == "standby"


@pytest.mark.asyncio
async def test_list_files_flattens_nested_folders() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "name": "sub",
                        "path": "sub",
                        "type": "folder",
                        "children": [
                            {
                                "name": "nested.gcode",
                                "path": "sub/nested.gcode",
                                "type": "machinecode",
                                "size": 9,
                            }
                        ],
                    },
                    {
                        "name": "top.gcode",
                        "path": "top.gcode",
                        "type": "machinecode",
                        "size": 5,
                    },
                ]
            },
        )

    files = await _client(handler).list_files()
    paths = {entry["path"] for entry in files}
    assert paths == {"sub/nested.gcode", "top.gcode"}


@pytest.mark.asyncio
async def test_upload_to_subfolder_posts_path_field(tmp_path: Path) -> None:
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={})

    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(handler)
    await client.upload(source, "sub/dir/cube.gcode")
    assert ("POST", "/api/files/local") in {(m, p) for m, p, _ in seen}
    body = next(content for method, path, content in seen if path == "/api/files/local")
    assert b'name="path"' in body
    assert b"sub/dir" in body


@pytest.mark.asyncio
async def test_file_operations_and_controls(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/files" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"name": "cube.gcode", "path": "cube.gcode", "size": 5, "type": "machinecode"}
                    ]
                },
            )
        return httpx.Response(200, json={})

    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(handler)
    assert (await client.list_files())[0]["filename"] == "cube.gcode"
    await client.upload(source, "cube.gcode")
    await client.start("cube.gcode")
    await client.delete_file("cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()
    assert ("POST", "/api/files/local") in seen
    assert ("POST", "/api/files/local/cube.gcode") in seen
    assert ("DELETE", "/api/files/local/cube.gcode") in seen
    assert ("POST", "/api/job") in seen


@pytest.mark.asyncio
async def test_auth_failure_has_stable_code() -> None:
    client = _client(lambda request: httpx.Response(403))
    with pytest.raises(OctoPrintError) as exc:
        await client.info()
    assert exc.value.code == "provider_authentication_failed"


@pytest.mark.asyncio
async def test_conflict_maps_to_no_active_job() -> None:
    client = _client(lambda request: httpx.Response(409))
    with pytest.raises(OctoPrintError) as exc:
        await client.pause()
    assert exc.value.code == "provider_no_active_job"


@pytest.mark.asyncio
async def test_remote_path_traversal_rejected(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(lambda request: httpx.Response(204))
    with pytest.raises(OctoPrintError) as exc:
        await client.upload(source, "../cube.gcode")
    assert exc.value.code == "provider_error"
