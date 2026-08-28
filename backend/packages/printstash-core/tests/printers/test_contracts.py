"""The structural protocols every printer transport is required to satisfy.

These are `Protocol` classes, not base classes, which is deliberate: a provider
client is written against a printer's API, not against a PrintStash hierarchy, and
several of them are thin wrappers over a third-party library. Structural typing
lets each one stay shaped by its protocol while the application still gets one
interface.

The cost of that choice is that nothing enforces conformance at import time. A
client missing a method type-checks fine until the application calls it, at which
point the failure is an `AttributeError` in a background poll — for one provider,
for whoever configured it. `PrinterClient` and `ArtifactCaptureClient` are
therefore `runtime_checkable`, and every provider's own test file asserts
`isinstance` against them. The rows here pin the protocol itself: that a
structurally complete client is accepted, and that an incomplete one is *not* —
because a `runtime_checkable` Protocol only checks method *names*, so the
guarantee is weaker than it looks and worth knowing the exact strength of.

`ArtifactCaptureClient` is separate rather than part of `PrinterClient` because
only some printers can hand back the bytes of a print they started themselves.
Merging the two would make every provider claim a capability two of them do not
have.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from printstash_core.printers import (
    ArtifactCaptureClient,
    Capability,
    PrinterClient,
    PrinterSnapshot,
    ProviderCapabilities,
    SnapshotCallback,
)

CAPABILITIES = ProviderCapabilities(frozenset(Capability))


class CompleteClient:
    """A structurally complete client, implementing nothing else."""

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
        return {}

    async def delete_file(self, remote_filename: str) -> Mapping[str, Any]:
        return {}

    async def start(self, remote_filename: str) -> Mapping[str, Any]:
        return {}

    async def pause(self) -> Mapping[str, Any]:
        return {}

    async def resume(self) -> Mapping[str, Any]:
        return {}

    async def cancel(self) -> Mapping[str, Any]:
        return {}

    async def run_gcode(self, script: str) -> Mapping[str, Any]:
        return {}

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


class TestPrinterClient:
    def test_accepts_a_structurally_complete_client(self) -> None:
        # No inheritance anywhere: a provider client is shaped by the printer's
        # API, and the protocol is what makes it usable by the application.
        assert isinstance(CompleteClient(), PrinterClient)

    def test_refuses_a_client_missing_a_method(self) -> None:
        class Incomplete:
            capabilities = CAPABILITIES

            async def info(self) -> Mapping[str, Any]:
                return {}

        # This is the strength of the guarantee: a missing *name* is caught at
        # the seam rather than as an AttributeError in a background poll.
        assert not isinstance(Incomplete(), PrinterClient)

    def test_accepts_a_client_whose_signatures_differ(self) -> None:
        class WrongSignature(CompleteClient):
            async def pause(self, unexpected: int) -> Mapping[str, Any]:  # type: ignore[override]
                return {}

        # And this is the limit of it: `runtime_checkable` compares names only.
        # Signatures are pyright's job, which is why the type check is a
        # required gate rather than a nicety.
        assert isinstance(WrongSignature(), PrinterClient)


class TestArtifactCaptureClient:
    def test_accepts_a_client_that_can_recover_artifact_bytes(self) -> None:
        assert isinstance(CaptureClient(), ArtifactCaptureClient)

    def test_refuses_a_client_that_cannot(self) -> None:
        # Kept separate from `PrinterClient` because only some printers can hand
        # back the bytes of a print they started themselves; merging them would
        # make every provider claim a capability two of them lack.
        assert not isinstance(CompleteClient(), ArtifactCaptureClient)
