"""Framework-neutral Elegoo Centauri Carbon provider client."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Never, Protocol, cast

from pycentauri.cc2 import CC2Printer
from pycentauri.client import Printer, PrinterError

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    Capability,
    ElegooCentauriConfig,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)


class ElegooCentauriError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error") -> None:
        super().__init__(detail)
        self.code = code


ELEGOO_CENTAURI_CAPABILITIES = ProviderCapabilities(
    supported=frozenset(
        {
            Capability.START,
            Capability.PAUSE,
            Capability.RESUME,
            Capability.CANCEL,
            Capability.LIVE_STATUS,
            Capability.SERVER_INFO,
            Capability.UPLOAD,
        }
    ),
    support_level="beta",
    support_notes=(
        "Centauri Carbon uses local SDCP; Carbon 2 uses local authenticated MQTT.",
        "Upload runs over plain HTTP, independent of the SDCP/MQTT control channel.",
        "File inventory, deletion, and print-history import remain unavailable.",
    ),
)


class _CentauriConnection(Protocol):
    async def status(self) -> object: ...

    def watch(self) -> AsyncIterator[object]: ...

    async def upload_file(
        self, local_path: str | Path, *, remote_name: str | None = None
    ) -> str: ...

    async def start_print(self, filename: str, **kwargs: Any) -> Any: ...

    async def pause(self) -> Any: ...

    async def resume(self) -> Any: ...

    async def stop(self) -> Any: ...

    async def close(self) -> None: ...


Connector = Callable[[bool], Awaitable[_CentauriConnection]]
AsyncAction = Callable[..., Awaitable[Any]]


async def _call_supported_kwargs(action: AsyncAction, *args: Any, **kwargs: Any) -> Any:
    """Call across the supported pycentauri range without guessing its version."""

    try:
        parameters = inspect.signature(action).parameters.values()
    except (TypeError, ValueError):
        return await action(*args, **kwargs)
    if not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    ):
        accepted = {parameter.name for parameter in parameters}
        kwargs = {key: value for key, value in kwargs.items() if key in accepted}
    return await action(*args, **kwargs)


class ElegooCentauriClient:
    """Client for both Centauri generations using pycentauri as transport."""

    capabilities = ELEGOO_CENTAURI_CAPABILITIES

    def __init__(
        self,
        config: ElegooCentauriConfig,
        *,
        connector: Connector | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.host = config.host
        self.model = config.model
        self.access_code = config.access_code
        self.mainboard_id = config.mainboard_id
        self._connector = connector or self._connect
        self._logger = logger or logging.getLogger(__name__)

    async def _connect(self, enable_control: bool) -> _CentauriConnection:
        try:
            if self.model == "elegoo_centauri_carbon_2":
                if not self.access_code:
                    raise ElegooCentauriError(
                        "elegoo_centauri_access_code_required",
                        code="provider_credentials_missing",
                    )
                connect = cast(AsyncAction, CC2Printer.connect)
                return cast(
                    _CentauriConnection,
                    await _call_supported_kwargs(
                        connect,
                        self.host,
                        enable_control=enable_control,
                        access_code=self.access_code,
                        mainboard_id=self.mainboard_id,
                    ),
                )
            connect = cast(AsyncAction, Printer.connect)
            return cast(
                _CentauriConnection,
                await _call_supported_kwargs(
                    connect,
                    self.host,
                    enable_control=enable_control,
                    mainboard_id=self.mainboard_id,
                ),
            )
        except ElegooCentauriError:
            raise
        except PrinterError as exc:
            detail = str(exc)
            code = (
                "provider_authentication_failed"
                if "access" in detail.lower() or "auth" in detail.lower()
                else "provider_transport_error"
            )
            raise ElegooCentauriError(detail, code=code) from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise ElegooCentauriError(str(exc)) from exc

    async def _close_quietly(self, connection: _CentauriConnection) -> None:
        try:
            await connection.close()
        except Exception as exc:
            self._logger.debug("centauri connection close failed: %s", exc)

    async def _with_connection(
        self,
        enable_control: bool,
        action: Callable[[_CentauriConnection], Awaitable[Any]],
    ) -> Any:
        connection = await self._connector(enable_control)
        try:
            return await action(connection)
        except ElegooCentauriError:
            raise
        except PrinterError as exc:
            raise ElegooCentauriError(str(exc)) from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise ElegooCentauriError(str(exc)) from exc
        finally:
            await self._close_quietly(connection)

    async def info(self) -> dict[str, Any]:
        return {
            "result": {
                "provider": "elegoo_centauri",
                "model": self.model,
                "host": self.host,
            }
        }

    async def server_info(self) -> dict[str, Any]:
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        self._unsupported()

    async def printer_config(self) -> dict[str, Any]:
        self._unsupported()

    async def query_status(self) -> dict[str, Any]:
        status = await self._with_connection(
            False, lambda connection: connection.status()
        )
        return {"result": {"status": self.normalize_status(status)}}

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot.from_legacy_payload(await self.query_status())

    @staticmethod
    def normalize_status(status: object) -> dict[str, Any]:
        print_state = getattr(status, "print_status", None)
        state_map = {
            0: "standby",
            1: "standby",
            5: "paused",
            6: "paused",
            7: "cancelled",
            8: "cancelled",
            9: "complete",
            10: "standby",
            11: "standby",
            12: "printing",
            13: "printing",
            14: "error",
            15: "standby",
            16: "standby",
            17: "standby",
            18: "printing",
            27: "paused",
            28: "paused",
            29: "paused",
        }
        state = state_map.get(
            cast(int, print_state),
            "standby" if print_state is None else "unknown",
        )
        raw_progress = getattr(status, "progress", 0)
        try:
            progress = float(raw_progress or 0) / 100.0
        except (TypeError, ValueError):
            progress = 0.0
        print_info = getattr(status, "print_info", None)
        raw = getattr(status, "raw", {})
        raw_mapping = raw if isinstance(raw, Mapping) else {}
        message = raw_mapping.get("Message") or raw_mapping.get("Error") or ""
        return {
            "print_stats": {
                "state": state,
                "filename": getattr(status, "filename", None),
                "message": str(message),
                "print_duration": (
                    getattr(print_info, "current_ticks", None)
                    if print_info is not None
                    else None
                ),
            },
            "virtual_sdcard": {"progress": max(0.0, min(1.0, progress))},
            "heater_bed": {
                "temperature": getattr(status, "temp_bed", None),
                "target": getattr(status, "temp_bed_target", None),
            },
            "extruder": {
                "temperature": getattr(status, "temp_nozzle", None),
                "target": getattr(status, "temp_nozzle_target", None),
            },
            "temperature_sensor chamber": {
                "temperature": getattr(status, "temp_chamber", None)
            },
        }

    async def list_files(self) -> list[Mapping[str, Any]]:
        self._unsupported()

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        remote_name = await self._with_connection(
            True,
            lambda connection: connection.upload_file(
                local_path, remote_name=remote_filename
            ),
        )
        return {"result": remote_name}

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        del remote_filename
        self._unsupported()

    async def start(self, remote_filename: str) -> dict[str, Any]:
        async def start_print(connection: _CentauriConnection) -> Any:
            action = cast(AsyncAction, connection.start_print)
            return await _call_supported_kwargs(
                action,
                remote_filename,
                storage="local",
                auto_leveling=True,
                timelapse=False,
            )

        await self._with_connection(True, start_print)
        return {"ok": True}

    async def pause(self) -> dict[str, Any]:
        await self._with_connection(True, lambda connection: connection.pause())
        return {"ok": True}

    async def resume(self) -> dict[str, Any]:
        await self._with_connection(True, lambda connection: connection.resume())
        return {"ok": True}

    async def cancel(self) -> dict[str, Any]:
        await self._with_connection(True, lambda connection: connection.stop())
        return {"ok": True}

    async def run_gcode(self, script: str) -> dict[str, Any]:
        del script
        self._unsupported()

    async def emergency_stop(self) -> dict[str, Any]:
        self._unsupported()

    @staticmethod
    def _unsupported() -> Never:
        raise ElegooCentauriError(
            "operation_not_supported_for_provider",
            code="operation_not_supported_for_provider",
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        connection = await self._connector(False)
        try:
            async for status in connection.watch():
                await on_status(self.normalize_status(status))
                if stop_event is not None and stop_event.is_set():
                    return
        except PrinterError as exc:
            raise ElegooCentauriError(str(exc)) from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise ElegooCentauriError(str(exc)) from exc
        finally:
            await self._close_quietly(connection)

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        async def convert(status: dict[str, Any]) -> None:
            await on_snapshot(PrinterSnapshot.from_legacy_payload(status))

        await self.subscribe_status(convert, stop_event=stop_event)


class ElegooCentauriFactory:
    provider_id = ProviderId.ELEGOO_CENTAURI
    capabilities = ELEGOO_CENTAURI_CAPABILITIES

    def __init__(
        self,
        *,
        connector: Connector | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._connector = connector
        self._logger = logger

    def build(self, config: PrinterConfig) -> PrinterClient:
        if not isinstance(config, ElegooCentauriConfig):
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return ElegooCentauriClient(
            config, connector=self._connector, logger=self._logger
        )
