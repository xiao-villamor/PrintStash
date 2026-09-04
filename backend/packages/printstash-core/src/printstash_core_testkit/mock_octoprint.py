# pyright: basic
"""A mock OctoPrint REST service for client contract tests.

Speaks the subset of the stable OctoPrint API the client drives: version,
printer/job status, recursive file listing (with folders, to exercise
``_flatten_files``), multipart upload, delete, select+print start, and the
``/api/job`` pause/resume/cancel command endpoint. ``X-Api-Key`` only — no
digest auth in real OctoPrint installs.

Explicitly asserts the security-relevant contract: a plain upload (no
``print=true``) never starts a print, matching ``OctoPrintClient.upload``'s
call shape (``select=false, print=false``).

Run standalone::

    python -m tests.e2e.fakes.mock_octoprint --port 5000 --print-seconds 5
"""

from __future__ import annotations

import argparse
import time
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile

from .print_sim import PrintSim

# print_stats.state -> OctoPrint state flags, mirrors OctoPrintClient._normalize_status.
# ponytail: real OctoPrint's "cancelling" flag is transient (clears once the
# cancel finishes); this fake holds it True until the next print starts so a
# 2s poll reliably observes it. Real-world implication: PrinterHub relies on
# catching that transient window, so a slow poll against a real OctoPrint
# instance can miss a cancel entirely — worth a follow-up if that's observed.
_ACTIVE_FLAGS = {
    "printing": {"printing": True},
    "paused": {"paused": True},
    "cancelled": {"cancelling": True},
    "error": {"error": True, "closedOrError": True},
}


def create_app(
    *,
    total_mm: float = 1200.0,
    total_seconds: float = 600.0,
    print_seconds: float = 5.0,
    api_key: Optional[str] = None,
) -> tuple[FastAPI, PrintSim]:
    sim = PrintSim(
        total_mm=total_mm, total_seconds=total_seconds, print_seconds=print_seconds
    )
    files: list[dict[str, Any]] = [
        {
            "type": "machinecode",
            "name": "existing.gcode",
            "path": "existing.gcode",
            "size": 42,
            "date": time.time(),
        }
    ]
    app = FastAPI()

    def _check_key(x_api_key: Optional[str]) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/api/version")
    async def version(x_api_key: Optional[str] = Header(None)) -> dict:
        _check_key(x_api_key)
        return {"api": "0.1", "server": "1.9.0", "text": "OctoPrint Mock"}

    @app.get("/api/printer")
    async def printer(x_api_key: Optional[str] = Header(None)) -> dict:
        _check_key(x_api_key)
        sim.progress()  # latch completion so flags reflect the current instant
        flags = {
            "operational": True,
            "printing": False,
            "paused": False,
            "cancelling": False,
            "error": False,
            "closedOrError": False,
            "ready": True,
        }
        flags.update(_ACTIVE_FLAGS.get(sim.state, {}))
        active = sim.is_active()
        return {
            "state": {"text": sim.state, "flags": flags},
            "temperature": {
                "bed": {
                    "actual": 60.0 if active else 25.0,
                    "target": 60.0 if active else 0.0,
                },
                "tool0": {
                    "actual": 210.0 if active else 25.0,
                    "target": 210.0 if active else 0.0,
                },
            },
        }

    @app.get("/api/job")
    async def job(x_api_key: Optional[str] = Header(None)) -> dict:
        _check_key(x_api_key)
        progress = sim.progress()
        return {
            "job": {
                "file": {"name": sim.filename, "path": sim.filename}
                if sim.filename
                else {},
            },
            "progress": {
                "completion": round(progress * 100, 2),
                "printTime": round(sim.elapsed(), 2),
            },
            "state": sim.state,
        }

    @app.post("/api/job")
    async def job_command(
        request: Request, x_api_key: Optional[str] = Header(None)
    ) -> Response:
        _check_key(x_api_key)
        body = await request.json()
        command = body.get("command")
        action = body.get("action")
        if command == "cancel":
            if not sim.is_active():
                raise HTTPException(status_code=409, detail="No active job")
            sim.cancel()
        elif command == "pause" and action == "pause":
            if sim.state != "printing":
                raise HTTPException(status_code=409, detail="Not printing")
            sim.pause()
        elif command == "pause" and action == "resume":
            if sim.state != "paused":
                raise HTTPException(status_code=409, detail="Not paused")
            sim.resume()
        else:
            raise HTTPException(status_code=400, detail="Unknown command")
        return Response(status_code=204)

    @app.get("/api/files")
    async def list_files(
        recursive: bool = False, x_api_key: Optional[str] = Header(None)
    ) -> dict:
        _check_key(x_api_key)
        return {"files": files}

    @app.post("/api/files/local")
    async def upload(
        request: Request, x_api_key: Optional[str] = Header(None)
    ) -> JSONResponse:
        _check_key(x_api_key)
        form = await request.form()
        upload_file = form["file"]
        if isinstance(upload_file, UploadFile):
            name = upload_file.filename or "upload.gcode"
            size = len(await upload_file.read())
        else:
            name = "upload.gcode"
            size = 0
        subdir = str(form.get("path", "") or "")
        path = f"{subdir}/{name}" if subdir else name
        files[:] = [f for f in files if f["path"] != path]
        files.append(
            {
                "type": "machinecode",
                "name": name,
                "path": path,
                "size": size,
                "date": time.time(),
            }
        )
        # Security contract: OctoPrintClient.upload always sends select=false,
        # print=false — a plain upload must never start a print.
        should_print = str(form.get("print", "")).lower() in ("true", "1", "yes")
        should_select = str(form.get("select", "")).lower() in ("true", "1", "yes")
        if should_print:
            sim.start(path)
        return JSONResponse(
            {"done": True, "files": {"local": {"name": name, "path": path}}},
            status_code=201 if not (should_select or should_print) else 200,
        )

    @app.delete("/api/files/local/{path:path}")
    async def delete_file(
        path: str, x_api_key: Optional[str] = Header(None)
    ) -> Response:
        _check_key(x_api_key)
        files[:] = [f for f in files if f["path"] != path]
        return Response(status_code=204)

    @app.post("/api/files/local/{path:path}")
    async def select_and_print(
        path: str, request: Request, x_api_key: Optional[str] = Header(None)
    ) -> Response:
        _check_key(x_api_key)
        body = await request.json()
        if body.get("command") == "select" and body.get("print"):
            sim.start(path)
        return Response(status_code=204)

    return app, sim


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock OctoPrint service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--total-mm", type=float, default=1200.0)
    parser.add_argument("--total-seconds", type=float, default=600.0)
    parser.add_argument("--print-seconds", type=float, default=5.0)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    import uvicorn

    app, _ = create_app(
        total_mm=args.total_mm,
        total_seconds=args.total_seconds,
        print_seconds=args.print_seconds,
        api_key=args.api_key,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
