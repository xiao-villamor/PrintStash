"""Framework-neutral OctoPrint HTTP provider client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Never
from urllib.parse import quote, unquote

import httpx

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    Capability,
    OctoPrintConfig,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)


class OctoPrintError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error") -> None:
        super().__init__(detail)
        self.code = code


OCTOPRINT_CAPABILITIES = ProviderCapabilities(
    supported=frozenset(
        {
            Capability.START,
            Capability.PAUSE,
            Capability.RESUME,
            Capability.CANCEL,
            Capability.LIVE_STATUS,
            Capability.UPLOAD,
            Capability.LIST_FILES,
            Capability.DELETE_FILE,
            Capability.SERVER_INFO,
        }
    ),
    support_level="beta",
    support_notes=(
        "OctoPrint support is beta pending broader hardware validation.",
        "Raw G-code controls and measured filament consumption are unavailable.",
    ),
)

HttpClientFactory = Callable[..., httpx.AsyncClient]


class OctoPrintClient:
    """Client for the stable OctoPrint REST API."""

    capabilities = OCTOPRINT_CAPABILITIES

    def __init__(
        self,
        config: OctoPrintConfig,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.timeout = timeout
        self.transport = transport
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    def _client(self) -> httpx.AsyncClient:
        return self._http_client_factory(
            base_url=self.base_url,
            headers={"Accept": "application/json", "X-Api-Key": self.api_key},
            timeout=self.timeout,
            transport=self.transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        allow_not_found = bool(kwargs.pop("allow_not_found", False))
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise OctoPrintError("octoprint_timeout", code="provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise OctoPrintError(str(exc)) from exc
        if response.status_code in {401, 403}:
            raise OctoPrintError(
                "octoprint_authentication_failed",
                code="provider_authentication_failed",
            )
        if response.status_code == 404:
            if allow_not_found:
                return {}
            raise OctoPrintError(
                "octoprint_endpoint_not_supported",
                code="provider_endpoint_not_supported",
            )
        if response.status_code == 409:
            raise OctoPrintError("octoprint_conflict", code="provider_no_active_job")
        if response.status_code >= 400:
            raise OctoPrintError(
                f"octoprint_http_{response.status_code}",
                code="provider_transport_error",
            )
        if response.status_code == 204 or not response.content:
            return {"ok": True}
        try:
            return response.json()
        except ValueError as exc:
            raise OctoPrintError(
                "octoprint_invalid_response", code="provider_invalid_response"
            ) from exc

    @staticmethod
    def _file_path(remote_filename: str) -> str:
        path = PurePosixPath(remote_filename.replace("\\", "/"))
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise OctoPrintError("remote_filename_invalid", code="provider_error")
        return "/".join(quote(part, safe="") for part in path.parts)

    async def info(self) -> dict[str, Any]:
        version = await self._request("GET", "/api/version")
        return {"result": {"provider": "octoprint", "version": version}}

    async def server_info(self) -> dict[str, Any]:
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        self._unsupported()

    async def printer_config(self) -> dict[str, Any]:
        self._unsupported()

    async def query_status(self) -> dict[str, Any]:
        printer, job = await asyncio.gather(
            self._request("GET", "/api/printer", allow_not_found=True),
            self._request("GET", "/api/job", allow_not_found=True),
        )
        return {"result": {"status": self._normalize_status(printer, job)}}

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot.from_legacy_payload(await self.query_status())

    @staticmethod
    def _normalize_status(
        printer: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        raw_printer_state = printer.get("state")
        printer_state: dict[str, Any] = (
            raw_printer_state if isinstance(raw_printer_state, dict) else {}
        )
        raw_flags = printer_state.get("flags")
        flags: dict[str, Any] = raw_flags if isinstance(raw_flags, dict) else {}
        raw_job = job.get("job")
        job_data: dict[str, Any] = raw_job if isinstance(raw_job, dict) else {}
        raw_progress = job.get("progress")
        progress: dict[str, Any] = (
            raw_progress if isinstance(raw_progress, dict) else {}
        )
        raw_file = job_data.get("file")
        file_data: dict[str, Any] = raw_file if isinstance(raw_file, dict) else {}
        completion = progress.get("completion")

        if flags.get("printing"):
            state = "printing"
        elif flags.get("paused") or flags.get("pausing"):
            state = "paused"
        elif flags.get("cancelling"):
            state = "cancelled"
        elif flags.get("error") or flags.get("closedOrError"):
            state = "error"
        elif (
            completion is not None
            and float(completion) >= 99.9
            and file_data.get("name")
        ):
            state = "complete"
        else:
            state = "standby"

        raw_temperature = printer.get("temperature")
        temperature: dict[str, Any] = (
            raw_temperature if isinstance(raw_temperature, dict) else {}
        )
        raw_bed = temperature.get("bed")
        bed: dict[str, Any] = raw_bed if isinstance(raw_bed, dict) else {}
        raw_tool0 = temperature.get("tool0")
        tool0: dict[str, Any] = raw_tool0 if isinstance(raw_tool0, dict) else {}

        return {
            "print_stats": {
                "state": state,
                "filename": file_data.get("name") or file_data.get("path"),
                "message": printer_state.get("text") or "",
                "print_duration": progress.get("printTime"),
            },
            "virtual_sdcard": {
                "progress": max(0.0, min(1.0, float(completion or 0) / 100.0))
            },
            "heater_bed": {
                "temperature": bed.get("actual"),
                "target": bed.get("target"),
            },
            "extruder": {
                "temperature": tool0.get("actual"),
                "target": tool0.get("target"),
            },
        }

    @classmethod
    def _flatten_files(cls, items: list[Any]) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "folder":
                children = item.get("children")
                if isinstance(children, list):
                    result.extend(cls._flatten_files(children))
                continue
            if item_type not in (None, "machinecode"):
                continue
            result.append(
                {
                    "path": item.get("path") or item.get("name"),
                    "filename": item.get("name") or item.get("path"),
                    "size": item.get("size"),
                    "modified": item.get("date"),
                }
            )
        return result

    async def list_files(self) -> list[Mapping[str, Any]]:
        body = await self._request("GET", "/api/files?recursive=true")
        files = body.get("files", body if isinstance(body, list) else [])
        if not isinstance(files, list):
            return []
        return self._flatten_files(files)

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        target = self._file_path(remote_filename)
        *parent_parts, filename = target.split("/")
        data = {"select": "false", "print": "false"}
        if parent_parts:
            data["path"] = "/".join(unquote(part) for part in parent_parts)
        with local_path.open("rb") as content:
            body = await self._request(
                "POST",
                "/api/files/local",
                files={"file": (filename, content, "application/octet-stream")},
                data=data,
            )
        return body if isinstance(body, dict) else {"ok": True}

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"/api/files/local/{self._file_path(remote_filename)}"
        )

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/files/local/{self._file_path(remote_filename)}",
            json={"command": "select", "print": True},
        )

    async def pause(self) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/job", json={"command": "pause", "action": "pause"}
        )

    async def resume(self) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/job", json={"command": "pause", "action": "resume"}
        )

    async def cancel(self) -> dict[str, Any]:
        return await self._request("POST", "/api/job", json={"command": "cancel"})

    async def run_gcode(self, script: str) -> dict[str, Any]:
        del script
        self._unsupported()

    async def emergency_stop(self) -> dict[str, Any]:
        self._unsupported()

    @staticmethod
    def _unsupported() -> Never:
        raise OctoPrintError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        status = await self.query_status()
        await on_status(status.get("result", {}).get("status", {}))
        if stop_event is None:
            await asyncio.sleep(2.0)
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            return

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        async def convert(status: dict[str, Any]) -> None:
            await on_snapshot(PrinterSnapshot.from_legacy_payload(status))

        await self.subscribe_status(convert, stop_event=stop_event)


class OctoPrintFactory:
    provider_id = ProviderId.OCTOPRINT
    capabilities = OCTOPRINT_CAPABILITIES

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._http_client_factory = http_client_factory

    def build(self, config: PrinterConfig) -> PrinterClient:
        if not isinstance(config, OctoPrintConfig):
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return OctoPrintClient(
            config,
            timeout=self._timeout,
            transport=self._transport,
            http_client_factory=self._http_client_factory,
        )
