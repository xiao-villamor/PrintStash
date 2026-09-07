"""Standalone real API/S3 fixture for the native-download browser proof.

Runs actual routers, authentication, persistence and storage clients. Background
workers are excluded, matching the backend E2E fixture. The ASGI observer counts
emitted bytes without changing requests or responses; TLS forwards to SeaweedFS.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import uvicorn
from sqlmodel import SQLModel

from app.db.session import get_session_factory
from app.main import app
from app.modules.identity.auth import create_access_token
from app.modules.storage.storage_backend.runtime import bind_backend
from tests.containers import shutdown_containers
from tests.factories import build_file, build_model, build_user
from tests.fakes.s3_delivery import browser_s3

PAYLOAD = b"solid native-browser\nendsolid native-browser\n"
FILENAME = "Piñón 🦾.stl"


def main() -> None:
    root = Path(os.environ["PLAYWRIGHT_DELIVERY_DATA_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PLAYWRIGHT_DELIVERY_API_PORT", "8435"))
    origin = os.environ.get("PLAYWRIGHT_DELIVERY_ORIGIN", "http://127.0.0.1:3335")
    observations = root / "responses.jsonl"
    observations.write_text("")
    try:
        with browser_s3(root, origin=origin) as (backend, tls):
            with get_session_factory().session() as session:
                SQLModel.metadata.create_all(session.get_bind())
                user = build_user(session, "native-browser-admin", superuser=True)
                model = build_model(session, "Native browser Artifact")
                key = backend.blob_key("native-browser", model.id, "part.stl")
                backend.write_bytes(PAYLOAD, key)
                artifact = build_file(
                    session,
                    model,
                    path=key,
                    filename=FILENAME,
                    size_bytes=len(PAYLOAD),
                    sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                )
                token = create_access_token(user.id, user.username, scope="admin")
                manifest = {
                    "path": f"/api/v1/files/{artifact.id}/download",
                    "providerOrigin": tls.endpoint,
                    "filename": FILENAME,
                    "payload": PAYLOAD.decode(),
                    "token": token,
                }
            bind_backend(backend)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            manifest_path.chmod(0o600)

            async def observed_app(scope, receive, send):
                observation = {"path": scope.get("path"), "bodyBytes": 0}

                async def observed_send(message):
                    if message["type"] == "http.response.start":
                        observation["status"] = message["status"]
                    elif message["type"] == "http.response.body":
                        observation["bodyBytes"] += len(message.get("body", b""))
                    await send(message)

                await app(scope, receive, observed_send)
                if scope["type"] == "http" and scope["path"] == manifest["path"]:
                    with observations.open("a") as output:
                        output.write(json.dumps(observation) + "\n")

            uvicorn.run(
                observed_app,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_level="warning",
            )
    finally:
        shutdown_containers()


if __name__ == "__main__":
    main()
