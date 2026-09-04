# pyright: basic
"""A mock PrusaLink v1 service for client contract tests.

Speaks the subset of the PrusaLink v1 REST API the client drives: status,
job, file list/upload/delete under ``/api/v1/files/local``, the OctoPrint-
compatible select+print start under ``/api/files/local``, and job
pause/resume/cancel. Two credential modes, both exercised by the client's
own tests: **digest** (401 challenge + ``WWW-Authenticate: Digest``, real
nonce validation via RFC 2617 MD5) and legacy **``X-Api-Key``**.

Run standalone::

    python -m tests.e2e.fakes.mock_prusalink --port 8080 --auth-mode api_key --api-key secret
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .print_sim import PrintSim

# ponytail: single process-wide nonce (no per-client nonce store / nonce-count
# replay tracking) — enough to exercise the real MD5 digest handshake
# httpx.DigestAuth performs; a multi-client nonce cache is the upgrade path
# if concurrent-auth tests ever need it.
_REALM = "PrusaLink"


def _digest_challenge(nonce: str) -> str:
    return f'Digest realm="{_REALM}", nonce="{nonce}", qop="auth", algorithm=MD5'


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 - RFC 2617 requires MD5


def _parse_digest_header(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in value[len("Digest ") :].split(","):
        if "=" not in chunk:
            continue
        key, _, raw = chunk.strip().partition("=")
        parts[key.strip()] = raw.strip().strip('"')
    return parts


def create_app(
    *,
    total_mm: float = 1200.0,
    total_seconds: float = 600.0,
    print_seconds: float = 5.0,
    auth_mode: str = "api_key",
    api_key: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> tuple[FastAPI, PrintSim]:
    sim = PrintSim(
        total_mm=total_mm, total_seconds=total_seconds, print_seconds=print_seconds
    )
    files: list[dict[str, Any]] = [
        {
            "name": "existing.gcode",
            "path": "existing.gcode",
            "size": 42,
            "m_timestamp": time.time(),
        },
        {
            "name": "demo.gcode",
            "path": "demo.gcode",
            "size": 42,
            "m_timestamp": time.time(),
        },
    ]
    nonce = secrets.token_hex(16)
    app = FastAPI()

    def _check_auth(
        request: Request, authorization: Optional[str], x_api_key: Optional[str]
    ) -> None:
        if auth_mode == "api_key":
            if api_key and x_api_key != api_key:
                raise HTTPException(
                    status_code=401,
                    headers={"WWW-Authenticate": _digest_challenge(nonce)},
                    detail="invalid api key",
                )
            return
        # digest
        if not authorization or not authorization.startswith("Digest "):
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": _digest_challenge(nonce)},
                detail="auth required",
            )
        parts = _parse_digest_header(authorization)
        if parts.get("nonce") != nonce or parts.get("username") != username:
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": _digest_challenge(nonce)},
                detail="bad credentials",
            )
        ha1 = _md5(f"{username}:{_REALM}:{password}")
        ha2 = _md5(f"{request.method}:{parts.get('uri', request.url.path)}")
        expected = _md5(
            f"{ha1}:{nonce}:{parts.get('nc', '')}:{parts.get('cnonce', '')}:"
            f"{parts.get('qop', 'auth')}:{ha2}"
        )
        if parts.get("response") != expected:
            raise HTTPException(
                status_code=401,
                headers={"WWW-Authenticate": _digest_challenge(nonce)},
                detail="bad digest response",
            )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            _check_auth(
                request,
                request.headers.get("authorization"),
                request.headers.get("x-api-key"),
            )
        except HTTPException as exc:
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
        return await call_next(request)

    @app.get("/api/v1/status")
    async def status() -> dict:
        sim.progress()
        active = sim.is_active()
        body: dict[str, Any] = {
            "printer": {
                "state": {
                    "printing": "PRINTING",
                    "paused": "PAUSED",
                    "complete": "FINISHED",
                    "cancelled": "STOPPED",
                    "error": "ERROR",
                    "standby": "IDLE",
                }[sim.state],
                "temp_bed": 60.0 if active else 25.0,
                "target_bed": 60.0 if active else 0.0,
                "temp_nozzle": 210.0 if active else 25.0,
                "target_nozzle": 210.0 if active else 0.0,
            }
        }
        if sim.filename:
            body["job"] = {
                "id": 1,
                "progress": round(sim.progress() * 100, 2),
                "time_printing": round(sim.elapsed(), 2),
            }
        return body

    @app.get("/api/v1/info")
    async def info() -> dict:
        return {
            "name": "Mock PrusaLink",
            "hostname": "mock-prusalink",
            "nozzle_diameter": 0.4,
        }

    @app.get("/api/v1/job")
    async def job() -> Any:
        progress = sim.progress()
        if not sim.filename:
            return Response(status_code=204)
        return {
            "id": 1,
            "state": sim.state,
            "progress": round(progress * 100, 2),
            "time_printing": round(sim.elapsed(), 2),
            "file": {"name": sim.filename, "path": f"/local/{sim.filename}"},
        }

    @app.get("/api/v1/files/local/{path:path}")
    async def file_info(path: str) -> dict:
        if path:
            for item in files:
                if item["path"] == path:
                    return {**item, "type": "PRINT_FILE", "read_only": False}
            raise HTTPException(status_code=404, detail="file not found")
        return {
            "name": "local",
            "type": "FOLDER",
            "read_only": False,
            "m_timestamp": int(time.time()),
            "children": [
                {**item, "type": "PRINT_FILE", "read_only": False} for item in files
            ],
        }

    @app.put("/api/v1/files/local/{path:path}")
    async def upload(path: str, request: Request) -> Response:
        body = await request.body()
        files[:] = [f for f in files if f["path"] != path]
        files.append(
            {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "size": len(body),
                "m_timestamp": time.time(),
            }
        )
        return Response(status_code=201)

    @app.delete("/api/v1/files/local/{path:path}")
    async def delete_file(path: str) -> Response:
        files[:] = [f for f in files if f["path"] != path]
        return Response(status_code=204)

    @app.post("/api/v1/files/local/{path:path}")
    async def start_print(path: str) -> Response:
        if not any(item["path"] == path for item in files):
            raise HTTPException(status_code=404, detail="file not found")
        if sim.is_active():
            raise HTTPException(status_code=409, detail="print already active")
        sim.start(path)
        return Response(status_code=204)

    @app.put("/api/v1/job/{job_id}/pause")
    async def pause(job_id: int) -> Response:
        if sim.state != "printing":
            raise HTTPException(status_code=409, detail="not printing")
        sim.pause()
        return Response(status_code=204)

    @app.put("/api/v1/job/{job_id}/resume")
    async def resume(job_id: int) -> Response:
        if sim.state != "paused":
            raise HTTPException(status_code=409, detail="not paused")
        sim.resume()
        return Response(status_code=204)

    @app.delete("/api/v1/job/{job_id}")
    async def cancel(job_id: int) -> Response:
        sim.cancel()
        return Response(status_code=204)

    return app, sim


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock PrusaLink service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--total-mm", type=float, default=1200.0)
    parser.add_argument("--total-seconds", type=float, default=600.0)
    parser.add_argument("--print-seconds", type=float, default=5.0)
    parser.add_argument("--auth-mode", choices=["api_key", "digest"], default="api_key")
    parser.add_argument("--api-key", default="secret")
    parser.add_argument("--username", default="maker")
    parser.add_argument("--password", default="secret")
    args = parser.parse_args()

    import uvicorn

    app, _ = create_app(
        total_mm=args.total_mm,
        total_seconds=args.total_seconds,
        print_seconds=args.print_seconds,
        auth_mode=args.auth_mode,
        api_key=args.api_key,
        username=args.username,
        password=args.password,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
