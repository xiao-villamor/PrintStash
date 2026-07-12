"""Local Elegoo Centauri Carbon / Carbon 2 provider client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from pycentauri.cc2 import CC2Printer
from pycentauri.client import Printer, PrinterError
from pycentauri.models import PrintStatus, Status


class ElegooCentauriError(RuntimeError):
    def __init__(self, detail: str, *, code: str = "provider_transport_error"):
        super().__init__(detail)
        self.code = code


class _CentauriConnection(Protocol):
    async def status(self) -> Status: ...

    def watch(self) -> AsyncIterator[Status]: ...

    async def start_print(self, filename: str, **kwargs: Any) -> Any: ...

    async def pause(self) -> Any: ...

    async def resume(self) -> Any: ...

    async def stop(self) -> Any: ...

    async def close(self) -> None: ...


Connector = Callable[[bool], Awaitable[_CentauriConnection]]


class ElegooCentauriClient:
    def __init__(
        self,
        host: str,
        *,
        model: str,
        access_code: str | None = None,
        mainboard_id: str | None = None,
        connector: Connector | None = None,
    ) -> None:
        self.host = host
        self.model = model
        self.access_code = access_code
        self.mainboard_id = mainboard_id
        self._connector = connector or self._connect

    async def _connect(self, enable_control: bool) -> _CentauriConnection:
        try:
            if self.model == "elegoo_centauri_carbon_2":
                if not self.access_code:
                    raise ElegooCentauriError(
                        "elegoo_centauri_access_code_required",
                        code="provider_credentials_missing",
                    )
                return await CC2Printer.connect(
                    self.host,
                    enable_control=enable_control,
                    access_code=self.access_code,
                    mainboard_id=self.mainboard_id,
                )
            return await Printer.connect(
                self.host,
                enable_control=enable_control,
                mainboard_id=self.mainboard_id,
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
            try:
                await connection.close()
            except Exception:
                # ponytail: close() is best-effort cleanup; a network drop
                # here shouldn't shadow the real error (or mask success).
                pass

    async def info(self) -> dict[str, Any]:
        return {
            "result": {
                "provider": "elegoo_centauri",
                "model": self.model,
                "host": self.host,
            }
        }

    async def query_status(self) -> dict[str, Any]:
        status = await self._with_connection(
            False, lambda connection: connection.status()
        )
        return {"result": {"status": self.normalize_status(status)}}

    @staticmethod
    def normalize_status(status: Status) -> dict[str, Any]:
        print_state = status.print_status
        state_map = {
            PrintStatus.IDLE: "standby",
            PrintStatus.HOMING: "standby",
            PrintStatus.PAUSING: "paused",
            PrintStatus.PAUSED: "paused",
            PrintStatus.STOPPING: "cancelled",
            PrintStatus.STOPPED: "cancelled",
            PrintStatus.COMPLETED: "complete",
            PrintStatus.FILE_CHECKING: "standby",
            PrintStatus.PRINTER_CHECKING: "standby",
            PrintStatus.RESUMING: "printing",
            PrintStatus.PRINTING: "printing",
            PrintStatus.ERROR: "error",
            PrintStatus.AUTO_LEVELING: "standby",
            PrintStatus.PREHEATING: "standby",
            PrintStatus.RESONANCE_TESTING: "standby",
            PrintStatus.PRINT_START: "printing",
            PrintStatus.FILAMENT_SWITCHING: "paused",
            PrintStatus.FILAMENT_LOAD_COMPLETE: "paused",
            PrintStatus.FILAMENT_UNLOADING: "paused",
        }
        state = state_map.get(
            print_state, "standby" if print_state is None else "unknown"
        )
        progress = float(status.progress or 0) / 100.0
        print_info = status.print_info
        message = status.raw.get("Message") or status.raw.get("Error") or ""
        return {
            "print_stats": {
                "state": state,
                "filename": status.filename,
                "message": str(message),
                "print_duration": print_info.current_ticks if print_info else None,
            },
            "virtual_sdcard": {"progress": max(0.0, min(1.0, progress))},
            "heater_bed": {
                "temperature": status.temp_bed,
                "target": status.temp_bed_target,
            },
            "extruder": {
                "temperature": status.temp_nozzle,
                "target": status.temp_nozzle_target,
            },
            "temperature_sensor chamber": {
                "temperature": status.temp_chamber,
            },
        }

    async def start(self, remote_filename: str) -> dict[str, Any]:
        await self._with_connection(
            True,
            lambda connection: connection.start_print(
                remote_filename,
                storage="local",
                auto_leveling=True,
                timelapse=False,
            ),
        )
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
            try:
                await connection.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
