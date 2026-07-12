"""Local OctoPrint HTTP provider client.

Supports the stable OctoPrint REST API (`/api/version`, `/api/printer`,
`/api/job`, `/api/files`) with `X-Api-Key` authentication. Responses are
normalized to the Moonraker-shaped snapshot consumed by PrinterHub.
"""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable
from urllib.parse import quote, unquote

import httpx


class OctoPrintError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error"):
        super().__init__(detail)
        self.code = code


class OctoPrintClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
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
                "octoprint_authentication_failed", code="provider_authentication_failed"
            )
        if response.status_code == 404:
            if allow_not_found:
                return {}
            raise OctoPrintError(
                "octoprint_endpoint_not_supported",
                code="provider_endpoint_not_supported",
            )
        if response.status_code == 409:
            # Conflict: e.g. no active job for pause/resume/cancel.
            raise OctoPrintError(
                "octoprint_conflict", code="provider_no_active_job"
            )
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

    async def query_status(self) -> dict[str, Any]:
        printer, job = await asyncio.gather(
            self._request("GET", "/api/printer", allow_not_found=True),
            self._request("GET", "/api/job", allow_not_found=True),
        )
        return {"result": {"status": self._normalize_status(printer, job)}}

    @staticmethod
    def _normalize_status(
        printer: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        flags = (
            printer.get("state", {}).get("flags", {})
            if isinstance(printer.get("state"), dict)
            else {}
        )
        job_data = job.get("job") if isinstance(job.get("job"), dict) else {}
        progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        file_data = (
            job_data.get("file") if isinstance(job_data.get("file"), dict) else {}
        )
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

        temperature = (
            printer.get("temperature", {}) if isinstance(printer.get("temperature"), dict) else {}
        )
        bed = temperature.get("bed") or {}
        tool0 = temperature.get("tool0") or {}

        return {
            "print_stats": {
                "state": state,
                "filename": file_data.get("name") or file_data.get("path"),
                "message": (
                    printer.get("state", {}).get("text")
                    if isinstance(printer.get("state"), dict)
                    else ""
                )
                or "",
                "print_duration": progress.get("printTime"),
            },
            "virtual_sdcard": {
                "progress": max(0.0, min(1.0, float(completion or 0) / 100.0)),
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
    def _flatten_files(cls, items: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
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

    async def list_files(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/files?recursive=true")
        files = body.get("files", body if isinstance(body, list) else [])
        if not isinstance(files, list):
            return []
        return self._flatten_files(files)

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        target = self._file_path(remote_filename)
        *parent_parts, filename = target.split("/")
        # OctoPrint's upload endpoint is always POST /api/files/local; a
        # subfolder target goes in the `path` form field, not the URL
        # (`/api/files/local/sub/dir` is not a valid endpoint and 404s).
        data = {"select": "false", "print": "false"}
        if parent_parts:
            data["path"] = "/".join(unquote(part) for part in parent_parts)
        # ponytail: whole-file read off the loop via a thread; fine for
        # typical gcode sizes. Chunked/streaming upload is the upgrade path
        # if hundreds-of-MB files start pressuring RAM.
        content = await asyncio.to_thread(local_path.read_bytes)
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
