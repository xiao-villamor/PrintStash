#!/usr/bin/env python3
"""Create immutable release demo backups, or recover them in a candidate image.

Uses the shipped HTTP API and disposable Docker volumes only. Run with the
backend virtualenv (httpx); no application modules or database edits are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx

USERNAME = "backup-demo"
PASSWORD = "DemoBackupOnly123!"
# Deliberately fake, public fixture value, as required by testing.md ("key").
# Only disposable demo volumes use this; never a deployed installation.
DEMO_SECRETS_KEY = "key"
FORMAT = 1


def docker(*args: str) -> str:
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def checked(response: httpx.Response, status: int = 200) -> httpx.Response:
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text}"
        )
    return response


def completed(api: httpx.Client, accepted: httpx.Response) -> dict:
    job_id = checked(accepted, 202).json()["job_id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        job = checked(api.get(f"/api/v1/jobs/{job_id}")).json()
        if job["state"] == "completed":
            return job
        if job["state"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Job {job_id}: {job}")
        time.sleep(0.25)
    raise TimeoutError(f"Job {job_id} did not complete")


def login(api: httpx.Client) -> None:
    result = checked(
        api.post(
            "/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}
        )
    ).json()
    api.headers["Authorization"] = f"Bearer {result['access_token']}"


@contextmanager
def installation(image: str, *, secrets_key: str):
    volume = docker("volume", "create", f"backup-compat-{uuid4().hex}")
    container = None
    try:
        container = docker(
            "run",
            "-d",
            "-p",
            "127.0.0.1::3000",
            "-v",
            f"{volume}:/data",
            "-e",
            "VAULT_SETUP_MODE=trusted_network",
            "-e",
            f"VAULT_SECRETS_KEY={secrets_key}",
            image,
        )
        port = docker("port", container, "3000/tcp").rsplit(":", 1)[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=300) as api:
            deadline = time.monotonic() + 120
            while True:
                try:
                    checked(api.get("/api/v1/health", timeout=2))
                    break
                except (httpx.HTTPError, RuntimeError) as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Image did not become healthy") from exc
                    time.sleep(0.5)
            api.headers["Origin"] = str(api.base_url).rstrip("/")
            csrf = checked(api.post("/api/v1/setup/session")).json()["csrf"]
            api.headers["X-PrintStash-Setup-CSRF"] = csrf
            setup = checked(
                api.post(
                    "/api/v1/setup",
                    json={
                        "username": USERNAME,
                        "password": PASSWORD,
                        "storage_backend": "local",
                        "data_dir": "/data/files",
                        "thumb_dir": "/data/thumbs",
                    },
                ),
                201,
            ).json()
            api.headers["Authorization"] = f"Bearer {setup['access_token']}"
            health = checked(api.get("/api/v1/health/details")).json()
            yield api, container, health
    except BaseException:
        if container:
            print(docker("logs", "--tail", "160", container))
        raise
    finally:
        if container:
            docker("rm", "-f", container)
        docker("volume", "rm", volume)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def capture_catalog(api: httpx.Client) -> list[dict]:
    models = checked(api.get("/api/v1/models", params={"limit": 100})).json()
    result = []
    for model in sorted(models, key=lambda row: row["id"]):
        detail = checked(api.get(f"/api/v1/models/{model['id']}")).json()
        artifacts = []
        for artifact in sorted(detail["files"], key=lambda row: row["id"]):
            payload = checked(
                api.get(f"/api/v1/files/{artifact['id']}/download")
            ).content
            artifacts.append(
                {
                    "id": artifact["id"],
                    "filename": artifact["original_filename"],
                    "size": len(payload),
                    "sha256": digest(payload),
                }
            )
        result.append({"id": model["id"], "name": detail["name"], "files": artifacts})
    return result


def asset_names(version: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must be X.Y.Z")
    return f"demo-backup-{version}.tar.gz", f"demo-backup-{version}.json"


def create(image: str, version: str, corpus: Path, output: Path) -> None:
    archive_name, oracle_name = asset_names(version)
    if (output / archive_name).exists() or (output / oracle_name).exists():
        raise FileExistsError(
            "Historical backup fixtures are immutable; choose an empty output directory"
        )
    sources = sorted(
        path
        for path in corpus.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".stl", ".3mf", ".gcode", ".bgcode"}
    )
    if not sources:
        raise ValueError("testdata contains no supported Artifacts")
    image_info = json.loads(docker("image", "inspect", image))[0]
    with installation(image, secrets_key=DEMO_SECRETS_KEY) as (api, _container, health):
        if health["version"] != version:
            raise ValueError(f"Expected released {version}, got {health}")
        inputs = []
        for path in sources:
            payload = path.read_bytes()
            relative = path.relative_to(corpus).as_posix()
            route = "orca" if path.suffix.lower() in {".gcode", ".bgcode"} else "model"
            completed(
                api,
                api.post(
                    f"/api/v1/ingest/{route}",
                    files={"file": (path.name, payload, "application/octet-stream")},
                    data={"model_name": f"Demo {version}: {relative}"},
                ),
            )
            inputs.append(
                {"path": relative, "size": len(payload), "sha256": digest(payload)}
            )
            print(f"Imported {relative}", flush=True)
        catalog = capture_catalog(api)
        stored = {
            artifact["sha256"] for model in catalog for artifact in model["files"]
        }
        if {item["sha256"] for item in inputs} != stored:
            raise AssertionError(
                "Every testdata source must remain downloadable byte-for-byte"
            )
        backup = completed(api, api.post("/api/v1/backups"))["result"]
        params = {"source_ref": backup["source_ref"]}
        verification = checked(
            api.post(f"/api/v1/backups/{backup['backup_id']}/verify", params=params)
        ).json()
        if not verification["valid"] or not verification["app_compatible"]:
            raise AssertionError(verification)
        archive = checked(
            api.get(f"/api/v1/backups/{backup['backup_id']}/download", params=params)
        ).content
        # The adoption endpoint accepts the archive's original generated filename.
        sources_view = checked(api.get("/api/v1/backups/sources")).json()
        source = next(
            row for row in sources_view if row["source_ref"] == backup["source_ref"]
        )
        # Recover the exact downloaded archive in an entirely new volume below.
        oracle = {
            "format": FORMAT,
            "version": version,
            "image_id": image_info["Id"],
            "image_digests": image_info["RepoDigests"],
            # The original key is required by the documented recovery procedure.
            "demo_secrets_key": DEMO_SECRETS_KEY,
            "archive_filename": Path(source["key"]).name,
            "backup_id": backup["backup_id"],
            "archive_sha256": digest(archive),
            "inputs": inputs,
            "catalog": catalog,
        }
    output.mkdir(parents=True, exist_ok=True)
    (output / archive_name).write_bytes(archive)
    (output / oracle_name).write_text(json.dumps(oracle, indent=2) + "\n")
    print(
        f"Created {output / archive_name}: {len(catalog)} Models, {len(stored)} original Artifacts",
        flush=True,
    )


def verify(image: str, version: str, fixture: Path, expected_version: str) -> None:
    archive_name, oracle_name = asset_names(version)
    oracle = json.loads((fixture / oracle_name).read_text())
    archive = (fixture / archive_name).read_bytes()
    if (
        oracle["format"] != FORMAT
        or oracle["version"] != version
        or digest(archive) != oracle["archive_sha256"]
    ):
        raise ValueError("Historical fixture identity/checksum mismatch")
    if oracle["demo_secrets_key"] != DEMO_SECRETS_KEY:
        raise ValueError("Fixture must use the documented public demo key")
    with installation(image, secrets_key=oracle["demo_secrets_key"]) as (
        api,
        container,
        health,
    ):
        if health["version"] != expected_version:
            raise ValueError(f"Expected candidate {expected_version}, got {health}")
        if capture_catalog(api) != []:
            raise AssertionError("Restore target must start empty")
        adopted = checked(
            api.post(
                "/api/v1/backups/upload",
                files={
                    "file": (oracle["archive_filename"], archive, "application/gzip")
                },
            ),
            201,
        ).json()
        checked(
            api.post(
                f"/api/v1/backups/{adopted['backup_id']}/restore",
                params={"source_ref": adopted["source_ref"]},
            )
        )
        login(api)
        actual = capture_catalog(api)
        if actual != oracle["catalog"]:
            raise AssertionError(
                f"Recovered catalog/bytes differ: expected {oracle['catalog']}, got {actual}"
            )
        # A database that only works until process restart is not recovered.
        docker("restart", "-t", "60", container)
        # Docker can allocate a different ephemeral host port on restart.
        # Rediscover the running container rather than polling its old address.
        port = docker("port", container, "3000/tcp").rsplit(":", 1)[1]
        api.base_url = f"http://127.0.0.1:{port}"
        api.headers["Origin"] = str(api.base_url).rstrip("/")
        deadline = time.monotonic() + 120
        while True:
            try:
                checked(api.get("/api/v1/health", timeout=2))
                break
            except (httpx.HTTPError, RuntimeError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Restored image did not restart") from exc
                time.sleep(0.5)
        login(api)
        if capture_catalog(api) != oracle["catalog"]:
            raise AssertionError("Recovered catalog/bytes changed after restart")
        completed(api, api.post("/api/v1/backups"))
    print(
        f"PASS: {version} backup -> {expected_version}; exact catalog/Artifact bytes, restart, subsequent backup",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("create")
    generate.add_argument("--image", required=True)
    generate.add_argument("--version", required=True)
    generate.add_argument("--testdata", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    recover = sub.add_parser("verify")
    recover.add_argument("--image", required=True)
    recover.add_argument("--version", required=True)
    recover.add_argument("--fixture", type=Path, required=True)
    recover.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    if args.command == "create":
        create(args.image, args.version, args.testdata, args.output)
    else:
        verify(args.image, args.version, args.fixture, args.expected_version)


if __name__ == "__main__":
    main()
