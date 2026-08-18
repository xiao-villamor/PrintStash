from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from printstash_core.printers import (
    ArtifactCaptureClient,
    Capability,
    MoonrakerConfig,
    OctoPrintConfig,
    PrinterClient,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    ProviderRegistry,
    SnapshotCallback,
)

CAPABILITIES = ProviderCapabilities(frozenset(Capability))


class FakeClient:
    capabilities = CAPABILITIES

    async def info(self) -> Mapping[str, Any]:
        return {}

    async def server_info(self) -> Mapping[str, Any]:
        return {}

    async def server_config(self) -> Mapping[str, Any]:
        return {}

    async def printer_config(self) -> Mapping[str, Any]:
        return {}

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot()

    async def list_files(self) -> list[Mapping[str, Any]]:
        return []

    async def upload(self, local_path: Path, remote_filename: str) -> Mapping[str, Any]:
        return {"path": local_path, "name": remote_filename}

    async def delete_file(self, remote_filename: str) -> Mapping[str, Any]:
        return {"name": remote_filename}

    async def start(self, remote_filename: str) -> Mapping[str, Any]:
        return {"name": remote_filename}

    async def pause(self) -> Mapping[str, Any]:
        return {}

    async def resume(self) -> Mapping[str, Any]:
        return {}

    async def cancel(self) -> Mapping[str, Any]:
        return {}

    async def run_gcode(self, script: str) -> Mapping[str, Any]:
        return {"script": script}

    async def emergency_stop(self) -> Mapping[str, Any]:
        return {}

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if stop_event is None or not stop_event.is_set():
            await on_snapshot(PrinterSnapshot())


class CaptureClient:
    async def download_artifact(
        self, remote_path: str, destination: Path, *, max_bytes: int
    ) -> None:
        del remote_path, destination, max_bytes


class FakeFactory:
    provider_id = ProviderId.MOONRAKER
    capabilities = CAPABILITIES

    def __init__(self) -> None:
        self.config: PrinterConfig | None = None

    def build(self, config: PrinterConfig) -> PrinterClient:
        self.config = config
        return FakeClient()


def test_runtime_protocols_accept_structural_clients() -> None:
    assert isinstance(FakeClient(), PrinterClient)
    assert isinstance(CaptureClient(), ArtifactCaptureClient)


def test_registry_register_build_and_capabilities() -> None:
    factory = FakeFactory()
    registry = ProviderRegistry([factory])
    config = MoonrakerConfig("http://printer.local")

    client = registry.build("moonraker", config)

    assert isinstance(client, PrinterClient)
    assert factory.config is config
    assert registry.capabilities(ProviderId.MOONRAKER) is CAPABILITIES
    assert registry.providers == (ProviderId.MOONRAKER,)


def test_registry_accepts_explicit_provider_id_registration() -> None:
    registry = ProviderRegistry()
    factory = FakeFactory()

    assert registry.register(ProviderId.MOONRAKER, factory) is factory


def test_registry_rejects_duplicates_unknowns_and_mismatched_configs() -> None:
    factory = FakeFactory()
    registry = ProviderRegistry([factory])

    with pytest.raises(ProviderError, match="provider_already_registered") as duplicate:
        registry.register(factory)
    assert duplicate.value.code == "provider_already_registered"

    with pytest.raises(ProviderError, match="unknown_provider") as unknown:
        registry.capabilities("not-a-provider")
    assert unknown.value.code == "unknown_provider"

    with pytest.raises(ProviderError, match="unknown_provider") as absent:
        ProviderRegistry().capabilities(ProviderId.MOONRAKER)
    assert absent.value.code == "unknown_provider"

    with pytest.raises(ProviderError, match="provider_config_mismatch") as mismatch:
        registry.build(
            ProviderId.MOONRAKER,
            OctoPrintConfig("http://octoprint.local", "key"),
        )
    assert mismatch.value.code == "provider_config_mismatch"
