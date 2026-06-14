"""Provider abstraction for printer backends (Moonraker, Bambu LAN)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import httpx
import paho.mqtt.client as mqtt

from app.core.logging import get_logger
from app.db.models import Printer, PrinterProvider
from app.services.moonraker import MoonrakerClient, MoonrakerError

logger = get_logger(__name__)


class ProviderError(RuntimeError):
    """Common provider exception surface."""

    def __init__(self, detail: str, *, code: str = "provider_error"):
        super().__init__(detail)
        self.detail = detail
        self.code = code


@dataclass(frozen=True)
class ProviderCapabilities:
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_live_status: bool
    can_upload: bool
    can_list_files: bool = False
    support_level: str = "stable"
    support_notes: tuple[str, ...] = ()
    unsupported_actions: tuple[str, ...] = ()


class PrinterProviderClient(Protocol):
    capabilities: ProviderCapabilities

    async def info(self) -> dict[str, Any]: ...

    async def server_info(self) -> dict[str, Any]: ...

    async def server_config(self) -> dict[str, Any]: ...

    async def printer_config(self) -> dict[str, Any]: ...

    async def query_status(self) -> dict[str, Any]: ...

    async def list_files(self) -> list[dict[str, Any]]: ...

    async def delete_file(self, remote_filename: str) -> dict[str, Any]: ...

    async def start(self, remote_filename: str) -> dict[str, Any]: ...

    async def pause(self) -> dict[str, Any]: ...

    async def resume(self) -> dict[str, Any]: ...

    async def cancel(self) -> dict[str, Any]: ...

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None: ...


class MoonrakerProvider:
    capabilities = ProviderCapabilities(
        can_start=True,
        can_pause=True,
        can_resume=True,
        can_cancel=True,
        can_live_status=True,
        can_upload=True,
        can_list_files=True,
        support_level="stable",
    )

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.client = MoonrakerClient(base_url, api_key)

    async def info(self) -> dict[str, Any]:
        try:
            return await self.client.info()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def server_info(self) -> dict[str, Any]:
        try:
            return await self.client.server_info()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def server_config(self) -> dict[str, Any]:
        try:
            return await self.client.server_config()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def printer_config(self) -> dict[str, Any]:
        try:
            return await self.client.query_configfile()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def query_status(self) -> dict[str, Any]:
        try:
            return await self.client.query_status()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def list_files(self) -> list[dict[str, Any]]:
        try:
            body = await self.client.list_gcode_files()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc
        result = body.get("result", [])
        return result if isinstance(result, list) else []

    async def start(self, remote_filename: str) -> dict[str, Any]:
        try:
            return await self.client.start_print(remote_filename)
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        try:
            return await self.client.delete_gcode_file(remote_filename)
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def pause(self) -> dict[str, Any]:
        try:
            return await self.client.pause_print()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def resume(self) -> dict[str, Any]:
        try:
            return await self.client.resume_print()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def cancel(self) -> dict[str, Any]:
        try:
            return await self.client.cancel_print()
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        try:
            await self.client.subscribe(on_status, stop_event=stop_event)
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc


class BambuLanProvider:
    capabilities = ProviderCapabilities(
        can_start=False,
        can_pause=True,
        can_resume=True,
        can_cancel=True,
        can_live_status=True,
        can_upload=False,
        can_list_files=False,
        support_level="beta",
        support_notes=(
            "Bambu LAN support is beta and currently limited to local status plus pause/resume/cancel controls.",
            "Vault upload, send-to-print, start existing files, and printer file inventory are not implemented for this provider yet.",
        ),
        unsupported_actions=("upload", "send", "start", "list_files"),
    )

    def __init__(self, host: str, serial: str, access_code: str) -> None:
        self.host = host
        self.serial = serial
        self.access_code = access_code
        self._request_topic = f"device/{serial}/request"
        self._report_topic = f"device/{serial}/report"

    def _mqtt_client(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set("bblp", self.access_code)
        client.tls_set()
        return client

    async def server_info(self) -> dict[str, Any]:
        raise ProviderError(
            "Provider does not expose Moonraker server info.",
            code="operation_not_supported_for_provider",
        )

    async def server_config(self) -> dict[str, Any]:
        raise ProviderError(
            "Provider does not expose Moonraker server config.",
            code="operation_not_supported_for_provider",
        )

    async def printer_config(self) -> dict[str, Any]:
        raise ProviderError(
            "Provider does not expose Klipper config.",
            code="operation_not_supported_for_provider",
        )

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        raise ProviderError(
            "Provider does not support remote file deletion.",
            code="operation_not_supported_for_provider",
        )

    async def _send_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        def _publish() -> None:
            client = self._mqtt_client()
            client.connect(self.host, 8883, keepalive=30)
            client.loop_start()
            client.publish(
                self._request_topic, json.dumps(payload), qos=1, retain=False
            )
            client.loop_stop()
            client.disconnect()

        try:
            await asyncio.to_thread(_publish)
            return {"ok": True}
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    def _normalize_status(self, report: dict[str, Any]) -> dict[str, Any]:
        print_report = report.get("print", {})
        gcode_state = str(print_report.get("gcode_state", "")).lower()
        progress = float(print_report.get("mc_percent", 0.0) or 0.0) / 100.0
        filename = print_report.get("subtask_name") or print_report.get("project_id")
        return {
            "print_stats": {
                "state": gcode_state,
                "filename": filename,
                "message": print_report.get("print_error") or "",
            },
            "virtual_sdcard": {
                "progress": max(0.0, min(1.0, progress)),
            },
        }

    async def info(self) -> dict[str, Any]:
        return {
            "result": {
                "provider": "bambu_lan",
                "host": self.host,
                "serial": self.serial,
            }
        }

    async def query_status(self) -> dict[str, Any]:
        url = f"https://{self.host}:6000/api/v1/status"
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.get(url)
            if resp.status_code >= 400:
                raise ProviderError(
                    f"bambu status http {resp.status_code}",
                    code="provider_transport_error",
                )
            body = resp.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc
        return {"result": {"status": self._normalize_status(body)}}

    async def list_files(self) -> list[dict[str, Any]]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def start(self, remote_filename: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def pause(self) -> dict[str, Any]:
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "pause"}}
        )

    async def resume(self) -> dict[str, Any]:
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "resume"}}
        )

    async def cancel(self) -> dict[str, Any]:
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "stop"}}
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        backoff = 1.0
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                status = await self.query_status()
                await on_status(status.get("result", {}).get("status", {}))
                backoff = 1.0
                await asyncio.sleep(2.0)
            except ProviderError as exc:
                logger.warning(
                    "bambu status poll failed for %s (%s), retry in %.1fs",
                    self.serial,
                    exc.code,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def capabilities_for_provider(provider: PrinterProvider) -> ProviderCapabilities:
    if provider == PrinterProvider.BAMBU_LAN:
        return BambuLanProvider.capabilities
    return MoonrakerProvider.capabilities


def provider_diagnostic_summary(provider: PrinterProvider) -> dict[str, object]:
    caps = capabilities_for_provider(provider)
    return {
        "provider": provider.value,
        "support_level": caps.support_level,
        "capabilities": {
            "can_start": caps.can_start,
            "can_pause": caps.can_pause,
            "can_resume": caps.can_resume,
            "can_cancel": caps.can_cancel,
            "can_live_status": caps.can_live_status,
            "can_upload": caps.can_upload,
            "can_list_files": caps.can_list_files,
        },
        "unsupported_actions": list(caps.unsupported_actions),
        "notes": list(caps.support_notes),
    }


def get_provider_client(printer: Printer) -> PrinterProviderClient:
    if printer.provider == PrinterProvider.BAMBU_LAN:
        if (
            not printer.bambu_host
            or not printer.bambu_serial
            or not printer.bambu_access_code
        ):
            raise ProviderError(
                "provider_credentials_missing",
                code="provider_credentials_missing",
            )
        return BambuLanProvider(
            host=printer.bambu_host,
            serial=printer.bambu_serial,
            access_code=printer.bambu_access_code,
        )

    if not printer.moonraker_url:
        raise ProviderError(
            "provider_credentials_missing",
            code="provider_credentials_missing",
        )
    return MoonrakerProvider(printer.moonraker_url, printer.api_key)
