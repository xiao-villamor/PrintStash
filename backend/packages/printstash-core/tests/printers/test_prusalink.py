"""The PrusaLink v1 client: authentication, path safety, and status shape.

PrusaLink is the firmware on a stock Prusa MK4/XL, and its API is the least
forgiving one PrintStash speaks. Three things make this file worth having.

**Two authentication modes, chosen by configuration.** Older firmware wants an
`X-Api-Key`; newer wants HTTP Digest with a username and password. Sending the
wrong one produces a 401 that looks exactly like a wrong credential, so the mode
must be honoured from the config without guessing.

**Remote filenames become URL path segments.** A name arriving from the library
is quoted per segment and refused outright if it could climb out of the storage
root. That refusal is the boundary: PrusaLink itself will happily act on
`../../..`, and the file being addressed is on the printer's SD card.

**The status shape shifts between firmware versions.** Telemetry appears under
`temp-bed` or `temp_bed` or flat on the printer object; the job appears at the
top level, nested under `job`, or embedded in the status response; progress is a
number or a `{completion}` mapping. All of them normalize onto one payload, and
the normalizer must not raise on a shape it has not seen — a poller that throws
takes the printer offline in the UI.

Wire-level behaviour against the real PrusaLink emulator lives in the backend's
contract tier; everything here is the client's own logic over a mock transport.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import (
    OctoPrintConfig,
    PrinterSnapshot,
    ProviderError,
    PrusaLinkConfig,
)
from printstash_core.printers.prusalink import (
    PRUSALINK_CAPABILITIES,
    PrusaLinkClient,
    PrusaLinkError,
    PrusaLinkFactory,
)

API_KEY = "key-123"
USERNAME = "maker"
PASSWORD = "not-a-real-password"

PRINTING_STATUS = {
    "printer": {
        "state": "PRINTING",
        "telemetry": {
            "temp-bed": {"actual": 59.5, "target": 60},
            "temp-nozzle": {"actual": 214, "target": 215},
        },
    }
}
PRINTING_JOB = {
    "id": 42,
    "state": "PRINTING",
    "file": {"name": "cube.gcode"},
    "progress": 25,
    "time_printing": 120,
    "time_remaining": 360,
}


def make_client(handler: Any, *, auth_mode: str = "api_key") -> PrusaLinkClient:
    return PrusaLinkClient(
        PrusaLinkConfig(
            "http://prusa.local/",
            auth_mode,
            username=USERNAME,
            password=PASSWORD,
            api_key=API_KEY,
        ),
        transport=httpx.MockTransport(handler),
    )


def responding(**by_path: dict[str, Any]) -> Any:
    """A handler answering the listed paths with JSON, 204 for anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = by_path.get(request.url.path)
        if body is None:
            return httpx.Response(204)
        return httpx.Response(200, json=body)

    return handler


def recording(seen: list[tuple[str, str]], **by_path: Any) -> Any:
    """A handler that records every (method, path) before answering."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        body = by_path.get(request.url.path)
        if body is None:
            return httpx.Response(204)
        return httpx.Response(200, json=body)

    return handler


def recording_raw(seen: list[str], **by_path: Any) -> Any:
    """Records the *encoded* path, which is what actually goes on the wire.

    `httpx.URL.path` is percent-decoded, so it cannot distinguish a literal
    separator from an escaped one — the very thing the quoting is for.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        body = by_path.get(request.url.path)
        if body is None:
            return httpx.Response(204)
        return httpx.Response(200, json=body)

    return handler


class TestClientSetup:
    @pytest.mark.asyncio
    async def test_sends_the_api_key_header_in_api_key_mode(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-Api-Key"))
            return httpx.Response(200, json={})

        await make_client(handler).info()

        assert seen == [API_KEY]

    @pytest.mark.asyncio
    async def test_omits_the_api_key_header_in_digest_mode(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-Api-Key"))
            return httpx.Response(200, json={})

        await make_client(handler, auth_mode="digest").info()

        # Newer firmware answers a stray API key with a 401 that is
        # indistinguishable from wrong digest credentials.
        assert seen == [None]

    @pytest.mark.asyncio
    async def test_answers_a_digest_challenge_with_the_configured_credentials(
        self,
    ) -> None:
        attempts: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            authorization = request.headers.get("Authorization")
            attempts.append(authorization)
            if authorization is None:
                return httpx.Response(
                    401,
                    headers={"WWW-Authenticate": 'Digest realm="Printer", nonce="abc"'},
                )
            return httpx.Response(200, json={})

        await make_client(handler, auth_mode="digest").info()

        assert attempts[0] is None
        assert f'username="{USERNAME}"' in str(attempts[1])

    def test_strips_a_trailing_slash_from_the_configured_url(self) -> None:
        client = make_client(responding())

        # Every path this client builds starts with `/`, so a retained trailing
        # slash produces `//api/v1/...`, which PrusaLink 404s.
        assert client.base_url == "http://prusa.local"


class TestRequest:
    @pytest.mark.asyncio
    async def test_returns_an_ok_marker_for_a_no_content_response(self) -> None:
        client = make_client(lambda _request: httpx.Response(204))

        assert await client.delete_file("cube.gcode") == {"ok": True}

    @pytest.mark.asyncio
    async def test_returns_an_ok_marker_for_an_empty_body(self) -> None:
        client = make_client(lambda _request: httpx.Response(200, content=b""))

        assert await client.delete_file("cube.gcode") == {"ok": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_reports_a_rejected_credential_as_an_authentication_failure(
        self, status_code: int
    ) -> None:
        client = make_client(lambda _request: httpx.Response(status_code))

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        # The one cause the operator can act on: the key or password is wrong.
        assert error.value.code == "provider_authentication_failed"

    @pytest.mark.asyncio
    async def test_reports_a_missing_endpoint_distinctly(self) -> None:
        client = make_client(lambda _request: httpx.Response(404))

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        # Older firmware genuinely lacks endpoints; that is a capability
        # problem, not an unreachable printer.
        assert error.value.code == "provider_endpoint_not_supported"

    @pytest.mark.asyncio
    async def test_tolerates_a_missing_endpoint_where_the_caller_allows_it(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/job":
                return httpx.Response(404)
            return httpx.Response(200, json=PRINTING_STATUS)

        # An idle printer has no job resource at all, which is not an error.
        status = await make_client(handler).query_status()

        assert status["result"]["status"]["print_stats"]["state"] == "printing"

    @pytest.mark.asyncio
    async def test_reports_a_server_error_as_a_transport_failure(self) -> None:
        client = make_client(lambda _request: httpx.Response(503))

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        assert error.value.code == "provider_transport_error"
        assert error.value.args[0] == "prusalink_http_503"

    @pytest.mark.asyncio
    async def test_reports_a_timeout_distinctly(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("printer did not answer")

        client = make_client(handler)

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        # A timeout is retryable in a way that a 4xx is not.
        assert error.value.code == "provider_timeout"

    @pytest.mark.asyncio
    async def test_wraps_a_connection_failure(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = make_client(handler)

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        assert error.value.code == "provider_transport_error"

    @pytest.mark.asyncio
    async def test_reports_a_body_that_is_not_json(self) -> None:
        client = make_client(
            lambda _request: httpx.Response(200, content=b"<html>login</html>")
        )

        with pytest.raises(PrusaLinkError) as error:
            await client.info()

        # A captive portal or a reverse proxy in front of the printer answers
        # 200 with HTML; that must not surface as a JSON decode traceback.
        assert error.value.code == "provider_invalid_response"


class TestFilePath:
    @pytest.mark.asyncio
    async def test_keeps_a_nested_path_as_separate_segments(self) -> None:
        seen: list[tuple[str, str]] = []
        client = make_client(recording(seen))

        await client.delete_file("folder/cube.gcode")

        assert seen == [("DELETE", "/api/v1/files/local/folder/cube.gcode")]

    @pytest.mark.asyncio
    async def test_percent_encodes_a_name_with_a_space(self) -> None:
        seen: list[str] = []
        client = make_client(recording_raw(seen))

        await client.delete_file("my part.gcode")

        assert seen == ["/api/v1/files/local/my%20part.gcode"]

    @pytest.mark.asyncio
    async def test_normalizes_a_windows_separator(self) -> None:
        seen: list[tuple[str, str]] = []
        client = make_client(recording(seen))

        await client.delete_file("folder\\cube.gcode")

        assert seen == [("DELETE", "/api/v1/files/local/folder/cube.gcode")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "remote_filename",
        ["../cube.gcode", "folder/../../cube.gcode", "/cube.gcode", "", "."],
    )
    async def test_refuses_a_name_that_could_leave_the_storage_root(
        self, remote_filename: str
    ) -> None:
        seen: list[tuple[str, str]] = []
        client = make_client(recording(seen))

        with pytest.raises(PrusaLinkError) as error:
            await client.delete_file(remote_filename)

        # PrusaLink would act on this path; the refusal has to happen here, and
        # before any request leaves.
        assert error.value.code == "provider_error"
        assert seen == []


class TestInfo:
    @pytest.mark.asyncio
    async def test_returns_the_printers_status_under_a_provider_envelope(self) -> None:
        client = make_client(responding(**{"/api/v1/status": PRINTING_STATUS}))

        assert await client.info() == {
            "result": {"provider": "prusalink", "status": PRINTING_STATUS}
        }


class TestServerInfo:
    @pytest.mark.asyncio
    async def test_answers_with_the_same_payload_as_info(self) -> None:
        client = make_client(responding(**{"/api/v1/status": PRINTING_STATUS}))

        # PrusaLink has no separate server endpoint; the capability is declared
        # supported, so it must answer with something rather than refuse.
        assert await client.server_info() == await client.info()


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_normalizes_a_printing_status_into_the_shared_envelope(self) -> None:
        client = make_client(
            responding(
                **{"/api/v1/status": PRINTING_STATUS, "/api/v1/job": PRINTING_JOB}
            )
        )

        assert await client.query_status() == {
            "result": {
                "status": {
                    "print_stats": {
                        "state": "printing",
                        "filename": "cube.gcode",
                        "message": "",
                        "print_duration": 120,
                    },
                    "virtual_sdcard": {"progress": 0.25},
                    "heater_bed": {"temperature": 59.5, "target": 60},
                    "extruder": {"temperature": 214, "target": 215},
                    "prusalink": {"job_id": 42, "time_remaining": 360},
                }
            }
        }


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("IDLE", "standby"),
            ("OPERATIONAL", "standby"),
            ("READY", "standby"),
            ("BUSY", "printing"),
            ("PRINTING", "printing"),
            ("PAUSED", "paused"),
            ("FINISHED", "complete"),
            ("COMPLETE", "complete"),
            ("STOPPED", "cancelled"),
            ("CANCELLED", "cancelled"),
            ("ERROR", "error"),
            ("ATTENTION", "error"),
        ],
    )
    def test_translates_every_prusalink_state(self, raw: str, normalized: str) -> None:
        status = PrusaLinkClient._normalize_status({"printer": {"state": raw}}, {})

        # `ATTENTION` is the filament-runout state; treating it as anything but
        # an error would leave the job apparently printing with no filament.
        assert status["print_stats"]["state"] == normalized

    def test_passes_an_unknown_state_through_lower_cased(self) -> None:
        status = PrusaLinkClient._normalize_status(
            {"printer": {"state": "CALIBRATING"}}, {}
        )

        assert status["print_stats"]["state"] == "calibrating"

    def test_defaults_to_standby_when_no_state_is_reported(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {})

        assert status["print_stats"]["state"] == "standby"

    def test_prefers_the_job_state_over_the_printer_state(self) -> None:
        status = PrusaLinkClient._normalize_status(
            {"printer": {"state": "BUSY"}}, {"state": "PAUSED"}
        )

        # The printer reports BUSY throughout a paused print; only the job
        # knows it is paused, which is what the operator needs to see.
        assert status["print_stats"]["state"] == "paused"

    def test_reads_a_job_nested_under_a_job_key(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {"job": {"id": 7}})

        assert status["prusalink"]["job_id"] == 7

    def test_reads_a_job_embedded_in_the_status_response(self) -> None:
        status = PrusaLinkClient._normalize_status({"job": {"id": 9}}, {})

        # Some firmware returns the job inline and 404s `/api/v1/job`.
        assert status["prusalink"]["job_id"] == 9

    def test_reads_progress_from_a_completion_mapping(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {"progress": {"completion": 40}})

        assert status["virtual_sdcard"] == {"progress": 0.4}

    def test_reads_progress_from_a_percentage_field(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {"progress_percent": 60})

        assert status["virtual_sdcard"] == {"progress": 0.6}

    @pytest.mark.parametrize(
        ("reported", "progress"), [(-10, 0.0), (140, 1.0), (None, 0.0), ("n/a", 0.0)]
    )
    def test_clamps_progress_into_the_unit_range(
        self, reported: object, progress: float
    ) -> None:
        status = PrusaLinkClient._normalize_status({}, {"progress": reported})

        assert status["virtual_sdcard"] == {"progress": progress}

    def test_reads_underscored_telemetry_keys(self) -> None:
        status = PrusaLinkClient._normalize_status(
            {
                "printer": {
                    "telemetry": {
                        "temp_bed": {"actual": 50, "target": 55},
                        "temp_nozzle": {"actual": 200, "target": 210},
                    }
                }
            },
            {},
        )

        # Firmware versions disagree on the separator, and a missed reading
        # renders as a printer with no temperatures at all.
        assert status["heater_bed"] == {"temperature": 50, "target": 55}
        assert status["extruder"] == {"temperature": 200, "target": 210}

    def test_reads_temperatures_reported_flat_on_the_printer(self) -> None:
        status = PrusaLinkClient._normalize_status(
            {
                "printer": {
                    "temp_bed": 51,
                    "target_bed": 56,
                    "temp_nozzle": 201,
                    "target_nozzle": 211,
                }
            },
            {},
        )

        assert status["heater_bed"] == {"temperature": 51, "target": 56}
        assert status["extruder"] == {"temperature": 201, "target": 211}

    def test_reports_no_temperatures_rather_than_raising_when_none_are_sent(
        self,
    ) -> None:
        status = PrusaLinkClient._normalize_status({"printer": {}}, {})

        assert status["heater_bed"] == {"temperature": None, "target": None}

    @pytest.mark.parametrize(
        "status_payload",
        [
            {"printer": "unavailable"},
            {"printer": {"telemetry": "unavailable"}},
            {"printer": {"telemetry": {"temp-bed": "unavailable"}}},
        ],
    )
    def test_survives_a_status_payload_of_the_wrong_shape(
        self, status_payload: dict[str, Any]
    ) -> None:
        # A normalizer that raises here takes the printer offline in the UI for
        # as long as the firmware keeps sending that shape.
        status = PrusaLinkClient._normalize_status(status_payload, {})

        assert status["print_stats"]["state"] == "standby"

    def test_falls_back_to_the_jobs_filename_field(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {"filename": "cube.gcode"})

        assert status["print_stats"]["filename"] == "cube.gcode"

    def test_falls_back_to_the_elapsed_time_field(self) -> None:
        status = PrusaLinkClient._normalize_status({}, {"time_elapsed": 90})

        assert status["print_stats"]["print_duration"] == 90

    def test_surfaces_an_error_message_from_the_status(self) -> None:
        status = PrusaLinkClient._normalize_status({"message": "Heating failed"}, {})

        assert status["print_stats"]["message"] == "Heating failed"


class TestQuerySnapshot:
    @pytest.mark.asyncio
    async def test_returns_the_same_reading_as_the_legacy_envelope(self) -> None:
        client = make_client(
            responding(
                **{"/api/v1/status": PRINTING_STATUS, "/api/v1/job": PRINTING_JOB}
            )
        )

        legacy = await client.query_status()
        snapshot = await client.query_snapshot()

        assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


class TestListFiles:
    @pytest.mark.asyncio
    async def test_flattens_a_folder_into_full_paths(self) -> None:
        client = make_client(
            responding(
                **{
                    "/api/v1/files/local/": {
                        "children": [
                            {
                                "name": "sub",
                                "type": "FOLDER",
                                "children": [
                                    {"name": "cube.gcode", "type": "PRINT_FILE"}
                                ],
                            }
                        ]
                    }
                }
            )
        )

        # The path is what `start` and `delete_file` are called with later, so a
        # bare filename here would address the wrong file.
        assert [item["path"] for item in await client.list_files()] == [
            "sub/cube.gcode"
        ]

    @pytest.mark.asyncio
    async def test_reports_file_size_alongside_modification_time(self) -> None:
        client = make_client(
            responding(
                **{
                    "/api/v1/files/local/": {
                        "children": [
                            {
                                "name": "cube.gcode",
                                "type": "PRINT_FILE",
                                "size": 2048,
                                "m_timestamp": 1700000000,
                            }
                        ]
                    }
                }
            )
        )

        assert await client.list_files() == [
            {
                "path": "cube.gcode",
                "filename": "cube.gcode",
                "size": 2048,
                "modified": 1700000000,
            }
        ]

    @pytest.mark.asyncio
    async def test_prefers_a_display_name_over_the_short_name(self) -> None:
        client = make_client(
            responding(
                **{
                    "/api/v1/files/local/": {
                        "children": [
                            {
                                "name": "CUBE~1.GCO",
                                "display_name": "cube.gcode",
                                "type": "PRINT_FILE",
                            }
                        ]
                    }
                }
            )
        )

        # PrusaLink reports 8.3 short names on FAT-formatted media; the display
        # name is the one an operator can recognize.
        assert (await client.list_files())[0]["filename"] == "cube.gcode"

    @pytest.mark.asyncio
    async def test_reads_a_listing_under_a_files_key(self) -> None:
        client = make_client(
            responding(
                **{
                    "/api/v1/files/local/": {
                        "files": [{"name": "cube.gcode", "type": "PRINT_FILE"}]
                    }
                }
            )
        )

        assert [item["path"] for item in await client.list_files()] == ["cube.gcode"]

    @pytest.mark.asyncio
    async def test_returns_nothing_for_an_empty_listing(self) -> None:
        client = make_client(responding(**{"/api/v1/files/local/": {"children": []}}))

        assert await client.list_files() == []

    @pytest.mark.asyncio
    async def test_reads_a_listing_returned_as_a_bare_array(self) -> None:
        client = make_client(
            lambda _request: httpx.Response(200, json=[{"name": "a.gcode"}])
        )

        # A plugin or a proxy in front of the printer can answer with an array.
        # `body.get` is evaluated before any inline fallback, so the list case
        # has to be tested first or this raises AttributeError out of the poll
        # loop.
        assert [item["path"] for item in await client.list_files()] == ["a.gcode"]

    @pytest.mark.asyncio
    async def test_returns_nothing_when_the_listing_is_the_wrong_shape(self) -> None:
        client = make_client(
            responding(**{"/api/v1/files/local/": {"children": "unavailable"}})
        )

        assert await client.list_files() == []

    @pytest.mark.asyncio
    async def test_skips_an_entry_that_is_not_a_mapping(self) -> None:
        client = make_client(
            responding(**{"/api/v1/files/local/": {"children": ["cube.gcode"]}})
        )

        assert await client.list_files() == []

    @pytest.mark.asyncio
    async def test_skips_a_folder_whose_children_are_the_wrong_shape(self) -> None:
        client = make_client(
            responding(
                **{
                    "/api/v1/files/local/": {
                        "children": [
                            {"name": "sub", "type": "FOLDER", "children": "broken"}
                        ]
                    }
                }
            )
        )

        # A folder is never itself a printable file, so it must not be listed
        # as one when its contents cannot be read.
        assert await client.list_files() == []


class TestUpload:
    @pytest.mark.asyncio
    async def test_puts_the_file_bytes_at_the_remote_path(self, tmp_path: Path) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        sent: list[tuple[str, str, bytes]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append((request.method, request.url.path, request.content))
            return httpx.Response(204)

        await make_client(handler).upload(source, "folder/cube.gcode")

        assert sent == [("PUT", "/api/v1/files/local/folder/cube.gcode", b"G28\n")]

    @pytest.mark.asyncio
    async def test_uploads_gcode_without_asking_the_printer_to_start(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers.append(request.headers)
            return httpx.Response(204)

        await make_client(handler).upload(source, "cube.gcode")

        # Without `Print-After-Upload: ?0` PrusaLink starts printing the file
        # the moment it lands, bypassing the queue entirely.
        assert headers[0]["Content-Type"] == "text/x.gcode"
        assert headers[0]["Print-After-Upload"] == "?0"
        assert headers[0]["Overwrite"] == "?1"

    @pytest.mark.asyncio
    async def test_returns_the_printers_own_response_body(self, tmp_path: Path) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        client = make_client(
            lambda _request: httpx.Response(201, json={"name": "cube.gcode"})
        )

        assert await client.upload(source, "cube.gcode") == {"name": "cube.gcode"}

    @pytest.mark.asyncio
    async def test_refuses_a_traversing_remote_name_before_reading_the_file(
        self, tmp_path: Path
    ) -> None:
        client = make_client(lambda _request: httpx.Response(204))

        with pytest.raises(PrusaLinkError) as error:
            await client.upload(tmp_path / "missing.gcode", "../cube.gcode")

        assert error.value.code == "provider_error"


class TestStart:
    @pytest.mark.asyncio
    async def test_posts_to_the_files_own_url(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(recording(seen)).start("folder/cube.gcode")

        assert seen == [("POST", "/api/v1/files/local/folder/cube.gcode")]


class TestActiveJobId:
    @pytest.mark.asyncio
    async def test_reads_the_id_from_a_flat_job_response(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(recording(seen, **{"/api/v1/job": {"id": 7}})).pause()

        assert ("PUT", "/api/v1/job/7/pause") in seen

    @pytest.mark.asyncio
    async def test_reads_the_id_from_a_nested_job_response(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(
            recording(seen, **{"/api/v1/job": {"job": {"id": 8}}})
        ).pause()

        assert ("PUT", "/api/v1/job/8/pause") in seen

    @pytest.mark.asyncio
    async def test_percent_encodes_an_id_that_is_not_a_plain_number(self) -> None:
        seen: list[str] = []

        await make_client(recording_raw(seen, **{"/api/v1/job": {"id": "a/b"}})).pause()

        # The id lands in a URL path; an unescaped separator would address a
        # different job resource entirely.
        assert "/api/v1/job/a%2Fb/pause" in seen

    @pytest.mark.asyncio
    async def test_refuses_to_act_when_there_is_no_active_job(self) -> None:
        client = make_client(responding(**{"/api/v1/job": {}}))

        with pytest.raises(PrusaLinkError) as error:
            await client.pause()

        # Naming the cause matters: "no active job" is a state the operator
        # understands, where a 404 from a guessed URL is not.
        assert error.value.code == "provider_no_active_job"


class TestPause:
    @pytest.mark.asyncio
    async def test_puts_to_the_jobs_pause_action(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(recording(seen, **{"/api/v1/job": {"id": 7}})).pause()

        assert ("PUT", "/api/v1/job/7/pause") in seen


class TestResume:
    @pytest.mark.asyncio
    async def test_puts_to_the_jobs_resume_action(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(recording(seen, **{"/api/v1/job": {"id": 7}})).resume()

        assert ("PUT", "/api/v1/job/7/resume") in seen


class TestCancel:
    @pytest.mark.asyncio
    async def test_deletes_the_job_resource(self) -> None:
        seen: list[tuple[str, str]] = []

        await make_client(recording(seen, **{"/api/v1/job": {"id": 7}})).cancel()

        # PrusaLink has no cancel action; deleting the job is the cancel.
        assert ("DELETE", "/api/v1/job/7") in seen


class TestUnsupportedOperations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "arguments"),
        [
            ("run_gcode", ("G28",)),
            ("emergency_stop", ()),
            ("server_config", ()),
            ("printer_config", ()),
        ],
    )
    async def test_refuses_before_opening_a_connection(
        self, method: str, arguments: tuple[Any, ...]
    ) -> None:
        seen: list[tuple[str, str]] = []
        client = make_client(recording(seen))

        with pytest.raises(PrusaLinkError) as error:
            await getattr(client, method)(*arguments)

        assert error.value.code == "operation_not_supported_for_provider"
        assert seen == []


class TestSubscribeStatus:
    @pytest.mark.asyncio
    async def test_delivers_the_current_status_once(self) -> None:
        stop = asyncio.Event()
        stop.set()
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await make_client(
            responding(**{"/api/v1/status": {"printer": {"state": "IDLE"}}})
        ).subscribe_status(on_status, stop_event=stop)

        # PrusaLink has no push channel, so "subscribing" is one poll; the hub
        # loops. The payload handed up is the status object, not the envelope.
        assert received[0]["print_stats"]["state"] == "standby"

    @pytest.mark.asyncio
    async def test_returns_after_one_poll_when_no_stop_event_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []

        async def record(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", record)
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await make_client(
            responding(**{"/api/v1/status": {"printer": {"state": "IDLE"}}})
        ).subscribe_status(on_status)

        # A caller with no stop event gets one reading, then the client paces
        # itself before returning so the hub's loop cannot become a busy poll of
        # the printer. The sleep is stubbed because the alternative is a test
        # that takes two real seconds to prove a constant.
        assert len(received) == 1
        assert slept == [2.0]

    @pytest.mark.asyncio
    async def test_returns_when_the_stop_event_never_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def timeout(awaitable: Any, timeout: float) -> None:
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", timeout)
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await make_client(
            responding(**{"/api/v1/status": {"printer": {"state": "IDLE"}}})
        ).subscribe_status(on_status, stop_event=asyncio.Event())

        # The wait is bounded, so a stop event that is never set returns rather
        # than parking the task forever — otherwise a printer removed from the
        # hub leaks its subscriber. `wait_for` is stubbed for the same reason as
        # above: the real bound is two seconds of wall clock.
        assert len(received) == 1


class TestSubscribeSnapshots:
    @pytest.mark.asyncio
    async def test_delivers_a_typed_snapshot(self) -> None:
        stop = asyncio.Event()
        stop.set()
        received: list[PrinterSnapshot] = []

        async def on_snapshot(snapshot: PrinterSnapshot) -> None:
            received.append(snapshot)

        await make_client(
            responding(**{"/api/v1/status": {"printer": {"state": "IDLE"}}})
        ).subscribe_snapshots(on_snapshot, stop_event=stop)

        assert received[0].state == "standby"


class TestCapabilities:
    def test_declares_itself_beta(self) -> None:
        assert PRUSALINK_CAPABILITIES.support_level == "beta"

    def test_satisfies_the_neutral_printer_protocol(self) -> None:
        client = PrusaLinkFactory().build(
            PrusaLinkConfig("http://prusa.local", "api_key", api_key=API_KEY)
        )

        assert isinstance(client, PrinterClient)


class TestPrusaLinkFactory:
    def test_builds_a_prusalink_client(self) -> None:
        config = PrusaLinkConfig("http://prusa.local", "api_key", api_key=API_KEY)

        client = PrusaLinkFactory().build(config)

        assert isinstance(client, PrusaLinkClient)
        assert client.config is config

    def test_passes_injected_transport_settings_through_to_the_client(self) -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(204))

        client = PrusaLinkFactory(timeout=3.0, transport=transport).build(
            PrusaLinkConfig("http://prusa.local", "api_key", api_key=API_KEY)
        )

        assert client.transport is transport
        assert client.timeout == 3.0

    def test_advertises_the_capabilities_before_building_a_client(self) -> None:
        assert PrusaLinkFactory().capabilities is PRUSALINK_CAPABILITIES

    def test_refuses_a_configuration_for_another_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            PrusaLinkFactory().build(OctoPrintConfig("http://octoprint.local", "key"))

        assert error.value.code == "provider_config_mismatch"
