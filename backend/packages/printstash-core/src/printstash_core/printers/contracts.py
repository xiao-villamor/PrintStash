"""Protocols implemented by printer transports and their factories."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import PrinterConfig, PrinterSnapshot, ProviderCapabilities, ProviderId

SnapshotCallback = Callable[[PrinterSnapshot], Awaitable[None]]


@runtime_checkable
class PrinterClient(Protocol):
    capabilities: ProviderCapabilities

    async def info(self) -> Mapping[str, Any]: ...

    async def server_info(self) -> Mapping[str, Any]: ...

    async def server_config(self) -> Mapping[str, Any]: ...

    async def printer_config(self) -> Mapping[str, Any]: ...

    async def query_snapshot(self) -> PrinterSnapshot: ...

    async def list_files(self) -> list[Mapping[str, Any]]: ...

    async def upload(
        self, local_path: Path, remote_filename: str
    ) -> Mapping[str, Any]: ...

    async def delete_file(self, remote_filename: str) -> Mapping[str, Any]: ...

    async def start(self, remote_filename: str) -> Mapping[str, Any]: ...

    async def pause(self) -> Mapping[str, Any]: ...

    async def resume(self) -> Mapping[str, Any]: ...

    async def cancel(self) -> Mapping[str, Any]: ...

    async def run_gcode(self, script: str) -> Mapping[str, Any]: ...

    async def emergency_stop(self) -> Mapping[str, Any]: ...

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None: ...


@runtime_checkable
class ArtifactCaptureClient(Protocol):
    async def download_artifact(
        self, remote_path: str, destination: Path, *, max_bytes: int
    ) -> None: ...


class ProviderFactory(Protocol):
    provider_id: ProviderId
    capabilities: ProviderCapabilities

    def build(self, config: PrinterConfig) -> PrinterClient: ...
