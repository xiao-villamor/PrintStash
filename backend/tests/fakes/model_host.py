"""Pinned model-file protocol over a real TLS socket, with controlled faults."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response

from app.modules.inference.manifest import read_manifest
from app.modules.inference.model_registry import DownloadAsset, RegistryEntry


@dataclass
class ModelHost:
    directory: Path
    entry: RegistryEntry
    fault: str | None = None
    calls: list[str] = field(default_factory=list)
    before_reply: Callable[[], None] | None = None

    @classmethod
    def from_directory(cls, directory: Path):
        key = json.loads((directory / "manifest.json").read_text())["model_key"]
        manifest = read_manifest(directory, key)
        files = tuple(
            DownloadAsset(
                asset.filename,
                asset.filename,
                (directory / asset.filename).stat().st_size,
                asset.sha256,
            )
            for asset in manifest.assets()
        )
        return cls(directory, RegistryEntry(manifest, files))

    def app(self):
        app = FastAPI()

        @app.get("/{repository:path}/resolve/{revision}/{filename}")
        def download(repository: str, revision: str, filename: str):
            self.calls.append(filename)
            if self.before_reply is not None:
                self.before_reply()
            if (
                repository != self.entry.manifest.repository
                or revision != self.entry.manifest.model_revision
            ):
                return Response(status_code=404)
            if filename not in {asset.filename for asset in self.entry.files}:
                return Response(status_code=404)
            if self.fault == "redirect":
                return RedirectResponse("https://untrusted.invalid/test-signed-secret")
            if self.fault == "loop":
                return RedirectResponse(f"/{repository}/resolve/{revision}/{filename}")
            payload = (self.directory / filename).read_bytes()
            if self.fault == "corrupt":
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            if self.fault == "oversize":
                payload += b"unexpected"
            if self.fault == "short":
                payload = payload[:-1]
            return Response(payload, media_type="application/octet-stream")

        return app
