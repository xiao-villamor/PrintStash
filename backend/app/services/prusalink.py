"""Local PrusaLink HTTP provider client.

Supports current v1 status, job, and file APIs plus PrusaLink's
OctoPrint-compatible file-start endpoint. Responses are normalized to the
Moonraker-shaped snapshot consumed by PrinterHub.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx


class PrusaLinkError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error"):
        super().__init__(detail)
        self.code = code


class PrusaLinkClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth_mode: str,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.username = username
        self.password = password
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if self.auth_mode == "digest":
            auth = httpx.DigestAuth(self.username or "", self.password or "")
        elif self.auth_mode == "api_key":
            headers["X-Api-Key"] = self.api_key or ""
        return httpx.AsyncClient(
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
                "prusalink_authentication_failed", code="provider_authentication_failed"
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

    async def query_status(self) -> dict[str, Any]:
        status, job = await asyncio.gather(
            self._request("GET", "/api/v1/status"),
            self._request("GET", "/api/v1/job", allow_not_found=True),
        )
        return {"result": {"status": self._normalize_status(status, job)}}

    @staticmethod
    def _normalize_status(
        status: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        printer = (
            status.get("printer") if isinstance(status.get("printer"), dict) else {}
        )
        job_data = job.get("job") if isinstance(job.get("job"), dict) else job
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
        file_data = (
            job_data.get("file") if isinstance(job_data.get("file"), dict) else {}
        )
        progress_data = job_data.get("progress")
        if isinstance(progress_data, dict):
            progress_value = progress_data.get("completion", 0)
        else:
            progress_value = progress_data or job_data.get("progress_percent", 0)
        try:
            progress = float(progress_value or 0)
        except (TypeError, ValueError):
            progress = 0.0
        # PrusaLink reports progress/completion on a 0-100 scale (both the
        # v1 `progress` number and the legacy `completion` field), never 0-1.
        progress /= 100.0
        telemetry = (
            printer.get("telemetry")
            if isinstance(printer.get("telemetry"), dict)
            else {}
        )
        temp = telemetry.get("temp-bed") or telemetry.get("temp_bed") or {}
        nozzle = telemetry.get("temp-nozzle") or telemetry.get("temp_nozzle") or {}
        return {
            "print_stats": {
                "state": state_map.get(raw_state, raw_state),
                "filename": file_data.get("name") or job_data.get("filename"),
                "message": job_data.get("message") or status.get("message") or "",
                "print_duration": job_data.get("time_printing")
                or job_data.get("time_elapsed"),
            },
            "virtual_sdcard": {"progress": max(0.0, min(1.0, progress))},
            "heater_bed": {
                "temperature": temp.get("actual"),
                "target": temp.get("target"),
            },
            "extruder": {
                "temperature": nozzle.get("actual"),
                "target": nozzle.get("target"),
            },
            "prusalink": {
                "job_id": job_data.get("id"),
                "time_remaining": job_data.get("time_remaining"),
            },
        }

    async def list_files(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v1/files/local")
        files = body.get("files", body if isinstance(body, list) else [])
        if not isinstance(files, list):
            return []
        result: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "path": item.get("path") or item.get("name"),
                    "filename": item.get("name") or item.get("path"),
                    "size": item.get("size"),
                    "modified": item.get("m_timestamp") or item.get("modified"),
                }
            )
        return result

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        target = self._file_path(remote_filename)
        # ponytail: whole-file read off the loop via a thread; fine for
        # typical gcode sizes. Chunked/streaming upload is the upgrade path
        # if hundreds-of-MB files start pressuring RAM.
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
        # PrusaLink retains OctoPrint-compatible select/print for an existing file.
        return await self._request(
            "POST",
            f"/api/files/local/{self._file_path(remote_filename)}",
            json={"command": "select", "print": True},
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
