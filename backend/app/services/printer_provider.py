"""Provider abstraction for printer backends (Moonraker, Bambu LAN)."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
from dataclasses import dataclass
from enum import StrEnum
from ftplib import FTP_TLS  # nosec B402 - Bambu LAN's implicit-TLS FTPS, not plaintext
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

import httpx
import paho.mqtt.client as mqtt

from app.core.logging import get_logger
from app.db.models import Printer, PrinterProvider
from app.services.elegoo_centauri import ElegooCentauriClient, ElegooCentauriError
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.prusalink import PrusaLinkClient, PrusaLinkError

logger = get_logger(__name__)


class _ImplicitFTP_TLS(FTP_TLS):
    """``ftplib`` client variant for Bambu's implicit-TLS port 990."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.source_address = source_address
        self.sock = socket.create_connection(
            (host, port), timeout, source_address=source_address
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class ProviderError(RuntimeError):
    """Common provider exception surface."""

    def __init__(self, detail: str, *, code: str = "provider_error"):
        super().__init__(detail)
        self.detail = detail
        self.code = code


class Capability(StrEnum):
    """Provider action vocabulary shared by API, UI, and future edge transport."""

    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    LIVE_STATUS = "live_status"
    UPLOAD = "upload"
    LIST_FILES = "list_files"
    SEND_GCODE = "send_gcode"
    MEASURED_CONSUMPTION = "measured_consumption"


@dataclass(frozen=True)
class ProviderCapabilities:
    supported: frozenset[Capability]
    support_level: str = "stable"
    support_notes: tuple[str, ...] = ()
    unsupported_actions: tuple[str, ...] = ()
    requires_ready_before_send: bool = False

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    @property
    def can_start(self) -> bool:
        return self.supports(Capability.START)

    @property
    def can_pause(self) -> bool:
        return self.supports(Capability.PAUSE)

    @property
    def can_resume(self) -> bool:
        return self.supports(Capability.RESUME)

    @property
    def can_cancel(self) -> bool:
        return self.supports(Capability.CANCEL)

    @property
    def can_live_status(self) -> bool:
        return self.supports(Capability.LIVE_STATUS)

    @property
    def can_upload(self) -> bool:
        return self.supports(Capability.UPLOAD)

    @property
    def can_list_files(self) -> bool:
        return self.supports(Capability.LIST_FILES)

    @property
    def can_send_gcode(self) -> bool:
        return self.supports(Capability.SEND_GCODE)

    @property
    def can_measure_consumption(self) -> bool:
        return self.supports(Capability.MEASURED_CONSUMPTION)

    def action_flags(self) -> dict[str, bool]:
        return {
            "can_start": self.can_start,
            "can_pause": self.can_pause,
            "can_resume": self.can_resume,
            "can_cancel": self.can_cancel,
            "can_live_status": self.can_live_status,
            "can_upload": self.can_upload,
            "can_list_files": self.can_list_files,
            "can_send_gcode": self.can_send_gcode,
            "can_measure_consumption": self.can_measure_consumption,
        }

    def as_api_dict(self) -> dict[str, object]:
        return {
            **self.action_flags(),
            "support_level": self.support_level,
            "support_notes": list(self.support_notes),
            "unsupported_actions": list(self.unsupported_actions),
        }


class PrinterProviderClient(Protocol):
    capabilities: ProviderCapabilities

    async def info(self) -> dict[str, Any]: ...

    async def server_info(self) -> dict[str, Any]: ...

    async def server_config(self) -> dict[str, Any]: ...

    async def printer_config(self) -> dict[str, Any]: ...

    async def query_status(self) -> dict[str, Any]: ...

    async def list_files(self) -> list[dict[str, Any]]: ...

    async def upload(
        self, local_path: Path, remote_filename: str
    ) -> dict[str, Any]: ...

    async def delete_file(self, remote_filename: str) -> dict[str, Any]: ...

    async def start(self, remote_filename: str) -> dict[str, Any]: ...

    async def pause(self) -> dict[str, Any]: ...

    async def resume(self) -> dict[str, Any]: ...

    async def cancel(self) -> dict[str, Any]: ...

    async def run_gcode(self, script: str) -> dict[str, Any]: ...

    async def emergency_stop(self) -> dict[str, Any]: ...

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None: ...


class MoonrakerProvider:
    capabilities = ProviderCapabilities(
        supported=frozenset(Capability),
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

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        try:
            return await self.client.upload_gcode(
                local_path, remote_filename, start_print=False
            )
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

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

    async def run_gcode(self, script: str) -> dict[str, Any]:
        try:
            return await self.client.run_gcode(script)
        except MoonrakerError as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def emergency_stop(self) -> dict[str, Any]:
        try:
            return await self.client.emergency_stop()
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
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
            }
        ),
        support_level="beta",
        support_notes=(
            "Bambu LAN upload and explicit start are beta features.",
            "Printer file inventory, deletion, raw G-code controls, and measured filament consumption are unavailable.",
        ),
        unsupported_actions=("list_files", "delete_file", "send_gcode"),
        requires_ready_before_send=True,
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

    def _upload_via_ftps(self, local_path: Path, remote_filename: str) -> None:
        """Store a plain-text G-code file in Bambu's cache over implicit FTPS."""

        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise ProviderError("invalid_bambu_remote_filename", code="provider_error")
        temp_name = f".{remote_name}.{uuid4().hex}.uploading"
        ftp = self._ftps_client()
        try:
            ftp.connect(self.host, 990)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()
            with local_path.open("rb") as source:
                ftp.storbinary(f"STOR cache/{temp_name}", source)
            remote_size = ftp.size(f"cache/{temp_name}")
            if remote_size is not None and remote_size != local_path.stat().st_size:
                raise ProviderError("bambu_upload_size_mismatch", code="provider_error")
            ftp.rename(f"cache/{temp_name}", f"cache/{remote_name}")
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001 - connection can fail before greeting
                try:
                    ftp.close()
                except Exception:  # noqa: BLE001 - best effort socket cleanup
                    pass

    @staticmethod
    def _ftps_client() -> FTP_TLS:
        context = ssl.create_default_context()
        # Bambu LAN devices expose a device-local self-signed certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return _ImplicitFTP_TLS(context=context, timeout=30)

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(self._upload_via_ftps, local_path, remote_filename)
            return {"ok": True, "remote_filename": remote_filename}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    # Bambu's print.gcode_state uses its own vocabulary (RUNNING/PAUSE/FINISH/
    # …). The rest of the pipeline — the coarse status map *and* the PrintJob
    # lifecycle sync — speaks Moonraker's print_stats.state terms, so translate
    # here. Without this, a paused or finished Bambu print read as UNKNOWN and
    # its job never transitioned to PAUSED/COMPLETED.
    _STATE_TO_MOONRAKER = {
        "idle": "standby",
        "prepare": "standby",
        "slicing": "standby",
        "running": "printing",
        "pause": "paused",
        "finish": "complete",
        "failed": "error",
    }

    def _normalize_status(self, report: dict[str, Any]) -> dict[str, Any]:
        print_report = report.get("print", {})
        raw_state = str(print_report.get("gcode_state", "")).lower()
        gcode_state = self._STATE_TO_MOONRAKER.get(raw_state, raw_state)
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
            # Bambu LAN mode serves a self-signed cert on the printer itself —
            # there is no CA to verify against, and self.host is a LAN IP the
            # user configured directly (not a name an attacker could spoof via
            # DNS). nosec: this is the documented way every Bambu LAN
            # integration talks to the printer's local API.
            async with httpx.AsyncClient(
                timeout=10.0,
                verify=False,  # nosec B501
            ) as client:
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
        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise ProviderError("invalid_bambu_remote_filename", code="provider_error")
        return await self._send_command(
            {
                "print": {
                    "sequence_id": uuid4().hex,
                    "command": "gcode_file",
                    "param": f"/cache/{remote_name}",
                }
            }
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

    async def run_gcode(self, script: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def emergency_stop(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        status = await self.query_status()
        await on_status(status.get("result", {}).get("status", {}))
        if stop_event is None:
            await asyncio.sleep(2.0)
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            return


class PrusaLinkProvider:
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
                Capability.LIST_FILES,
            }
        ),
        support_level="beta",
        support_notes=(
            "PrusaLink local FDM support is beta pending broader hardware validation.",
            "Raw G-code controls and measured filament consumption are unavailable.",
        ),
        unsupported_actions=("send_gcode", "emergency_stop"),
    )

    def __init__(self, client: PrusaLinkClient) -> None:
        self.client = client

    async def _call(self, method: str, *args: Any) -> Any:
        try:
            return await getattr(self.client, method)(*args)
        except PrusaLinkError as exc:
            raise ProviderError(str(exc), code=exc.code) from exc

    async def info(self) -> dict[str, Any]:
        return await self._call("info")

    async def server_info(self) -> dict[str, Any]:
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def printer_config(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def query_status(self) -> dict[str, Any]:
        return await self._call("query_status")

    async def list_files(self) -> list[dict[str, Any]]:
        return await self._call("list_files")

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        return await self._call("upload", local_path, remote_filename)

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        return await self._call("delete_file", remote_filename)

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self._call("start", remote_filename)

    async def pause(self) -> dict[str, Any]:
        return await self._call("pause")

    async def resume(self) -> dict[str, Any]:
        return await self._call("resume")

    async def cancel(self) -> dict[str, Any]:
        return await self._call("cancel")

    async def run_gcode(self, script: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def emergency_stop(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        try:
            await self.client.subscribe_status(on_status, stop_event=stop_event)
        except PrusaLinkError as exc:
            raise ProviderError(str(exc), code=exc.code) from exc


class ElegooCentauriProvider:
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
            }
        ),
        support_level="beta",
        support_notes=(
            "Centauri Carbon uses local SDCP; Carbon 2 uses local authenticated MQTT.",
            "Upload and file inventory are unavailable because current firmware does not expose a safe confirmed file API.",
        ),
        unsupported_actions=(
            "upload",
            "list_files",
            "delete_file",
            "send_gcode",
            "measured_consumption",
        ),
    )

    def __init__(self, client: ElegooCentauriClient) -> None:
        self.client = client

    async def _call(self, method: str, *args: Any) -> Any:
        try:
            return await getattr(self.client, method)(*args)
        except ElegooCentauriError as exc:
            raise ProviderError(str(exc), code=exc.code) from exc

    async def info(self) -> dict[str, Any]:
        return await self._call("info")

    async def server_info(self) -> dict[str, Any]:
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def printer_config(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def query_status(self) -> dict[str, Any]:
        return await self._call("query_status")

    async def list_files(self) -> list[dict[str, Any]]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self._call("start", remote_filename)

    async def pause(self) -> dict[str, Any]:
        return await self._call("pause")

    async def resume(self) -> dict[str, Any]:
        return await self._call("resume")

    async def cancel(self) -> dict[str, Any]:
        return await self._call("cancel")

    async def run_gcode(self, script: str) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def emergency_stop(self) -> dict[str, Any]:
        raise ProviderError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        try:
            await self.client.subscribe_status(on_status, stop_event=stop_event)
        except ElegooCentauriError as exc:
            raise ProviderError(str(exc), code=exc.code) from exc


def capabilities_for_provider(provider: PrinterProvider) -> ProviderCapabilities:
    if provider == PrinterProvider.BAMBU_LAN:
        return BambuLanProvider.capabilities
    if provider == PrinterProvider.PRUSALINK:
        return PrusaLinkProvider.capabilities
    if provider == PrinterProvider.ELEGOO_CENTAURI:
        return ElegooCentauriProvider.capabilities
    return MoonrakerProvider.capabilities


def provider_diagnostic_summary(provider: PrinterProvider) -> dict[str, object]:
    caps = capabilities_for_provider(provider)
    return {
        "provider": provider.value,
        "support_level": caps.support_level,
        "capabilities": caps.action_flags(),
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

    if printer.provider == PrinterProvider.PRUSALINK:
        if not printer.prusalink_url or not printer.prusalink_auth_mode:
            raise ProviderError(
                "provider_credentials_missing", code="provider_credentials_missing"
            )
        if printer.prusalink_auth_mode == "digest" and (
            not printer.prusalink_username or not printer.prusalink_password
        ):
            raise ProviderError(
                "provider_credentials_missing", code="provider_credentials_missing"
            )
        if printer.prusalink_auth_mode == "api_key" and not printer.prusalink_api_key:
            raise ProviderError(
                "provider_credentials_missing", code="provider_credentials_missing"
            )
        return PrusaLinkProvider(
            PrusaLinkClient(
                printer.prusalink_url,
                auth_mode=printer.prusalink_auth_mode,
                username=printer.prusalink_username,
                password=printer.prusalink_password,
                api_key=printer.prusalink_api_key,
            )
        )

    if printer.provider == PrinterProvider.ELEGOO_CENTAURI:
        if not printer.elegoo_centauri_host or printer.provider_variant not in {
            "elegoo_centauri_carbon",
            "elegoo_centauri_carbon_2",
        }:
            raise ProviderError(
                "provider_credentials_missing",
                code="provider_credentials_missing",
            )
        if (
            printer.provider_variant == "elegoo_centauri_carbon_2"
            and not printer.elegoo_centauri_access_code
        ):
            raise ProviderError(
                "provider_credentials_missing",
                code="provider_credentials_missing",
            )
        return ElegooCentauriProvider(
            ElegooCentauriClient(
                printer.elegoo_centauri_host,
                model=printer.provider_variant,
                access_code=printer.elegoo_centauri_access_code,
                mainboard_id=printer.elegoo_centauri_mainboard_id,
            )
        )

    if not printer.moonraker_url:
        raise ProviderError(
            "provider_credentials_missing",
            code="provider_credentials_missing",
        )
    return MoonrakerProvider(printer.moonraker_url, printer.api_key)
