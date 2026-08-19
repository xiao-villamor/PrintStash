"""Framework-neutral PrusaLink HTTP provider client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Never
from urllib.parse import quote

import httpx

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    Capability,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    PrusaLinkConfig,
)


class PrusaLinkError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error") -> None:
        super().__init__(detail)
        self.code = code


PRUSALINK_CAPABILITIES = ProviderCapabilities(
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
        "PrusaLink local FDM support is beta pending broader hardware validation.",
        "Raw G-code controls and measured filament consumption are unavailable.",
    ),
)

HttpClientFactory = Callable[..., httpx.AsyncClient]


class PrusaLinkClient:
    """Client for PrusaLink v1 using a typed core configuration."""

    capabilities = PRUSALINK_CAPABILITIES

    def __init__(
        self,
        config: PrusaLinkConfig,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.auth_mode = config.auth_mode
        self.username = config.username
        self.password = config.password
        self.api_key = config.api_key
        self.timeout = timeout
        self.transport = transport
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    def _client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if self.auth_mode == "digest":
            auth = httpx.DigestAuth(self.username or "", self.password or "")
        elif self.auth_mode == "api_key":
            headers["X-Api-Key"] = self.api_key or ""
        return self._http_client_factory(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            timeout=self.timeout,
            transport=self.transport,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        allow_not_found = bool(kwargs.pop("allow_not_found", False))
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise PrusaLinkError("prusalink_timeout", code="provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise PrusaLinkError(str(exc)) from exc
        if response.status_code in {401, 403}:
            raise PrusaLinkError(
                "prusalink_authentication_failed",
                code="provider_authentication_failed",
            )
        if response.status_code == 404:
            if allow_not_found:
                return {}
            raise PrusaLinkError(
                "prusalink_endpoint_not_supported",
                code="provider_endpoint_not_supported",
            )
        if response.status_code >= 400:
            raise PrusaLinkError(
                f"prusalink_http_{response.status_code}",
                code="provider_transport_error",
            )
        if response.status_code == 204 or not response.content:
            return {"ok": True}
        try:
            return response.json()
        except ValueError as exc:
            raise PrusaLinkError(
                "prusalink_invalid_response", code="provider_invalid_response"
            ) from exc

    @staticmethod
    def _file_path(remote_filename: str) -> str:
        path = PurePosixPath(remote_filename.replace("\\", "/"))
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise PrusaLinkError("remote_filename_invalid", code="provider_error")
        return "/".join(quote(part, safe="") for part in path.parts)

    async def info(self) -> dict[str, Any]:
        status = await self._request("GET", "/api/v1/status")
        return {"result": {"provider": "prusalink", "status": status}}

    async def server_info(self) -> dict[str, Any]:
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        self._unsupported()

    async def printer_config(self) -> dict[str, Any]:
        self._unsupported()

    async def query_status(self) -> dict[str, Any]:
        status, job = await asyncio.gather(
            self._request("GET", "/api/v1/status"),
            self._request("GET", "/api/v1/job", allow_not_found=True),
        )
        return {"result": {"status": self._normalize_status(status, job)}}

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot.from_legacy_payload(await self.query_status())

    @staticmethod
    def _normalize_status(
        status: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        raw_printer = status.get("printer")
        printer: dict[str, Any] = raw_printer if isinstance(raw_printer, dict) else {}
        raw_job = job.get("job")
        if isinstance(raw_job, dict):
            job_data: dict[str, Any] = raw_job
        elif job:
            job_data = job
        else:
            embedded_job = status.get("job")
            job_data = embedded_job if isinstance(embedded_job, dict) else {}
        raw_state = str(
            job_data.get("state")
            or printer.get("state")
            or status.get("state")
            or "idle"
        ).lower()
        state_map = {
            "idle": "standby",
            "operational": "standby",
            "ready": "standby",
            "busy": "printing",
            "printing": "printing",
            "paused": "paused",
            "finished": "complete",
            "complete": "complete",
            "stopped": "cancelled",
            "cancelled": "cancelled",
            "error": "error",
            "attention": "error",
        }
        raw_file = job_data.get("file")
        file_data: dict[str, Any] = raw_file if isinstance(raw_file, dict) else {}
        progress_data = job_data.get("progress")
        if isinstance(progress_data, dict):
            progress_value = progress_data.get("completion", 0)
        else:
            progress_value = progress_data or job_data.get("progress_percent", 0)
        try:
            progress = float(progress_value or 0)
        except (TypeError, ValueError):
            progress = 0.0
        progress /= 100.0
        raw_telemetry = printer.get("telemetry")
        telemetry: dict[str, Any] = (
            raw_telemetry if isinstance(raw_telemetry, dict) else {}
        )
        raw_temp = telemetry.get("temp-bed") or telemetry.get("temp_bed")
        temp: dict[str, Any] = raw_temp if isinstance(raw_temp, dict) else {}
        raw_nozzle = telemetry.get("temp-nozzle") or telemetry.get("temp_nozzle")
        nozzle: dict[str, Any] = raw_nozzle if isinstance(raw_nozzle, dict) else {}
        bed_actual = printer.get("temp_bed", temp.get("actual"))
        bed_target = printer.get("target_bed", temp.get("target"))
        nozzle_actual = printer.get("temp_nozzle", nozzle.get("actual"))
        nozzle_target = printer.get("target_nozzle", nozzle.get("target"))
        return {
            "print_stats": {
                "state": state_map.get(raw_state, raw_state),
                "filename": file_data.get("name") or job_data.get("filename"),
                "message": job_data.get("message") or status.get("message") or "",
                "print_duration": job_data.get("time_printing")
                or job_data.get("time_elapsed"),
            },
            "virtual_sdcard": {"progress": max(0.0, min(1.0, progress))},
            "heater_bed": {"temperature": bed_actual, "target": bed_target},
            "extruder": {
                "temperature": nozzle_actual,
                "target": nozzle_target,
            },
            "prusalink": {
                "job_id": job_data.get("id"),
                "time_remaining": job_data.get("time_remaining"),
            },
        }

    async def list_files(self) -> list[Mapping[str, Any]]:
        body = await self._request("GET", "/api/v1/files/local/")
        files = body.get(
            "children", body.get("files", body if isinstance(body, list) else [])
        )
        if not isinstance(files, list):
            return []
        result: list[Mapping[str, Any]] = []

        def append_items(items: list[Any], parent: str = "") -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("display_name") or "")
                path = str(
                    item.get("path")
                    or "/".join(part for part in (parent, name) if part)
                )
                if str(item.get("type", "")).upper() == "FOLDER":
                    children = item.get("children")
                    if isinstance(children, list):
                        append_items(children, path)
                    continue
                result.append(
                    {
                        "path": path or name,
                        "filename": item.get("display_name") or name or path,
                        "size": item.get("size"),
                        "modified": item.get("m_timestamp") or item.get("modified"),
                    }
                )

        append_items(files)
        return result

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        target = self._file_path(remote_filename)
        content = await asyncio.to_thread(local_path.read_bytes)
        body = await self._request(
            "PUT",
            f"/api/v1/files/local/{target}",
            content=content,
            headers={
                "Content-Type": "text/x.gcode",
                "Overwrite": "?1",
                "Print-After-Upload": "?0",
            },
        )
        return body if isinstance(body, dict) else {"ok": True}

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"/api/v1/files/local/{self._file_path(remote_filename)}"
        )

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/v1/files/local/{self._file_path(remote_filename)}"
        )

    async def _active_job_id(self) -> str:
        body = await self._request("GET", "/api/v1/job")
        job = body.get("job") if isinstance(body.get("job"), dict) else body
        job_id = job.get("id")
        if job_id is None:
            raise PrusaLinkError(
                "prusalink_no_active_job", code="provider_no_active_job"
            )
        return quote(str(job_id), safe="")

    async def pause(self) -> dict[str, Any]:
        return await self._request(
            "PUT", f"/api/v1/job/{await self._active_job_id()}/pause"
        )

    async def resume(self) -> dict[str, Any]:
        return await self._request(
            "PUT", f"/api/v1/job/{await self._active_job_id()}/resume"
        )

    async def cancel(self) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"/api/v1/job/{await self._active_job_id()}"
        )

    async def run_gcode(self, script: str) -> dict[str, Any]:
        del script
        self._unsupported()

    async def emergency_stop(self) -> dict[str, Any]:
        self._unsupported()

    @staticmethod
    def _unsupported() -> Never:
        raise PrusaLinkError(
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


class PrusaLinkFactory:
    provider_id = ProviderId.PRUSALINK
    capabilities = PRUSALINK_CAPABILITIES

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
        if not isinstance(config, PrusaLinkConfig):
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return PrusaLinkClient(
            config,
            timeout=self._timeout,
            transport=self._transport,
            http_client_factory=self._http_client_factory,
        )
