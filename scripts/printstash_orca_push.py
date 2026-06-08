#!/usr/bin/env python3
"""
PrintStash OrcaSlicer post-processing hook.

Configure in OrcaSlicer:
    Process → Others → Post-processing scripts:
        /usr/bin/python3 /path/to/printstash_orca_push.py \
            --url https://printstash.example.com \
            --username YOUR_USERNAME \
            --password YOUR_PASSWORD \
            --collection "Functional/Brackets"

OrcaSlicer appends the exported .gcode path automatically as the final argv.

Design rules (do not break):
  * stdlib only — no `pip install requests` for end users.
  * Always exit 0 — a PrintStash outage MUST NEVER block the slicing/export flow.
  * Logs to ~/.printstash_orca_push.log for debugging.
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

LOG_PATH = Path.home() / ".printstash_orca_push.log"
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("printstash_orca_push")


def build_multipart(fields: dict, file_path: Path) -> tuple[bytes, str]:
    """Build a multipart/form-data body using stdlib only."""
    boundary = f"----PrintStash{uuid.uuid4().hex}"
    crlf = "\r\n"
    body = bytearray()

    for key, value in fields.items():
        if value is None:
            continue
        body += f"--{boundary}{crlf}".encode()
        body += (
            f'Content-Disposition: form-data; name="{key}"{crlf}{crlf}{value}{crlf}'
        ).encode()

    mime, _ = mimetypes.guess_type(file_path.name)
    mime = mime or "application/octet-stream"
    body += f"--{boundary}{crlf}".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"{crlf}'
        f"Content-Type: {mime}{crlf}{crlf}"
    ).encode()
    body += file_path.read_bytes()
    body += crlf.encode()
    body += f"--{boundary}--{crlf}".encode()

    return bytes(body), f"multipart/form-data; boundary={boundary}"


def login(url: str, username: str, password: str) -> str | None:
    endpoint = url.rstrip("/") + "/api/v1/auth/login"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urlrequest.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "PrintStash-OrcaHook/1.0")
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            token = payload.get("access_token")
            if isinstance(token, str) and token:
                return token
            log.error("login response did not include access_token")
            return None
    except HTTPError as e:
        err_body = (
            e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        )
        log.error("login HTTP %s: %s", e.code, err_body)
    except URLError as e:
        log.error("login network error: %s", e.reason)
    except Exception as e:  # noqa: BLE001 — defensive: never crash the slicer
        log.error("login unexpected error: %s", e)
    return None


def push(url: str, token: str, gcode: Path, fields: dict, retries: int = 3) -> bool:
    endpoint = url.rstrip("/") + "/api/v1/ingest/orca"
    body, content_type = build_multipart(fields, gcode)

    for attempt in range(1, retries + 1):
        req = urlrequest.Request(endpoint, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("User-Agent", "PrintStash-OrcaHook/1.0")
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
                log.info("upload OK [%s] %s -> %s", resp.status, gcode.name, payload)
                return True
        except HTTPError as e:
            err_body = (
                e.read().decode("utf-8", errors="replace")
                if hasattr(e, "read")
                else ""
            )
            log.error("HTTP %s on attempt %d: %s", e.code, attempt, err_body)
            if 400 <= e.code < 500:
                # Client error — won't be fixed by retry.
                return False
        except URLError as e:
            log.error("network error attempt %d: %s", attempt, e.reason)
        except Exception as e:  # noqa: BLE001 — defensive: never crash the slicer
            log.error("unexpected error attempt %d: %s", attempt, e)

        time.sleep(2 ** attempt)

    return False


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--url",
        default=os.environ.get("PRINTSTASH_URL"),
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("PRINTSTASH_USERNAME"),
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("PRINTSTASH_PASSWORD"),
    )
    parser.add_argument("--collection", default=None)
    parser.add_argument("--tags", default=None)
    parser.add_argument("--model-name", default=None)
    args, positional = parser.parse_known_args()

    if not positional:
        log.error("no gcode path provided by OrcaSlicer")
        return 0  # never block export
    gcode = Path(positional[-1])
    if not gcode.exists():
        log.error("file not found: %s", gcode)
        return 0
    if not args.url or not args.username or not args.password:
        log.warning(
            "missing --url, --username, or --password — skipping PrintStash push"
        )
        return 0

    fields = {
        "model_name": args.model_name or gcode.stem,
        "collection": args.collection,
        "tags": args.tags,
    }

    token = login(args.url, args.username, args.password)
    if not token:
        log.warning("PrintStash login failed; export still proceeds")
        return 0

    ok = push(args.url, token, gcode, fields)
    if not ok:
        log.warning("PrintStash push failed; export still proceeds")

    # Always exit 0 — PrintStash problems must not break slicing.
    return 0


if __name__ == "__main__":
    sys.exit(main())
