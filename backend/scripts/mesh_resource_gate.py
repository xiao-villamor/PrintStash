"""Exercise the shipped API and DBOS engine inside a hard Linux memory ceiling.

Run with uv run python scripts/mesh_resource_gate.py --image IMAGE --memory-gib 1.
Issue attachments are optional local acceptance input, never downloaded by CI.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.factories import content
from tests.factories.geometry import expanding_three_mf, three_mf

MIB = 1024 * 1024


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, timeout=60).strip()


class Gate:
    def __init__(self, image: str, memory: int, report: Path):
        self.image, self.memory, self.report_path = image, memory, report
        self.name = "printstash-mesh-" + uuid.uuid4().hex[:12]
        self.volume = self.name + "-data"
        self.origin = ""
        self.token = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.report: dict = {
            "memory_gib": memory,
            "image": image,
            "cases": [],
            "rss_samples": [],
            "passed": False,
        }

    def request(self, path: str, body=None, *, method="GET", content_type=None):
        headers = {}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        if path == "/api/v1/setup/session":
            headers["Origin"] = self.origin
        if path == "/api/v1/setup":
            headers["Origin"] = self.origin
            headers["X-PrintStash-Setup-CSRF"] = self.csrf
        request = urllib.request.Request(
            self.origin + path, data=body, headers=headers, method=method
        )
        with self.opener.open(
            request, timeout=10 if path == "/api/v1/health" else 60
        ) as response:
            data = response.read()
            if response.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(data)
            return data

    def wait(self, predicate, *, timeout=360):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            # Health is part of every poll, including while a native worker runs.
            self.request("/api/v1/health")
            value = predicate()
            if value:
                return value
            time.sleep(0.5)
        raise AssertionError("production work did not settle before its deadline")

    def upload(self, name: str, data: bytes, *, archive=False) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode()
            + data
            + f"\r\n--{boundary}--\r\n".encode()
        )
        path = "/api/v1/ingest/archive/inspect" if archive else "/api/v1/ingest/model"
        accepted = self.request(
            path,
            body,
            method="POST",
            content_type="multipart/form-data; boundary=" + boundary,
        )
        job_id = accepted["job_id"]
        job = self.wait(lambda: self.terminal_job(job_id))
        return job

    def terminal_job(self, job_id):
        job = self.request("/api/v1/jobs/" + job_id)
        return job if job["state"] in ("completed", "failed", "cancelled") else None

    def rows(self, sql: str) -> list[dict]:
        script = (
            "import sqlite3,json; c=sqlite3.connect('/data/db/printstash.sqlite'); "
            "c.row_factory=sqlite3.Row; "
            "print(json.dumps([dict(r) for r in c.execute(" + repr(sql) + ")]))"
        )
        return json.loads(
            docker("exec", self.name, "/app/.venv/bin/python", "-c", script)
        )

    def settled_derivatives(self, file_id):
        rows = self.request(f"/api/v1/files/{file_id}/derivatives")
        applicable = [r for r in rows if r["kind"] in ("metadata", "thumbnail")]
        return (
            applicable
            if applicable
            and all(r["state"] in ("ready", "failed", "skipped") for r in applicable)
            and not self.rows(
                "SELECT kind FROM artifact_derivatives "
                f"WHERE file_id={file_id} AND next_attempt_at IS NOT NULL"
            )
            else None
        )

    def sample(self):
        script = r"""import json,pathlib
p=pathlib.Path('/proc')
workers=[]
rss=0
for d in p.iterdir():
 if not d.name.isdigit(): continue
 try:
  command=(d/'cmdline').read_bytes().replace(b'\0',b' ')
  if b'uvicorn' in command and b'app.main:app' in command:
   status=(d/'status').read_text()
   rss=max(rss,int(next(l.split()[1] for l in status.splitlines() if l.startswith('VmRSS:')))*1024)
  if b'app.modules.media.worker_bootstrap' in command:
   workers.append(int(d.name))
 except (OSError,StopIteration): pass
events=dict(l.split() for l in pathlib.Path('/sys/fs/cgroup/memory.events').read_text().splitlines())
print(json.dumps({'rss':rss,'workers':workers,'oom_kill':int(events['oom_kill']),
 'cgroup_current':int(pathlib.Path('/sys/fs/cgroup/memory.current').read_text()),
 'cgroup_peak':int(pathlib.Path('/sys/fs/cgroup/memory.peak').read_text())}))
"""
        # The script's own command line contains worker names; filter its PID.
        script = script.replace(
            "workers.append(int(d.name))",
            "workers.append(int(d.name)) if int(d.name)!=__import__('os').getpid() else None",
        )
        return json.loads(
            docker("exec", self.name, "/app/.venv/bin/python", "-c", script)
        )

    def case(self, name: str, data: bytes, *, must_succeed=False):
        started = time.monotonic()
        job = self.upload(name, data)
        assert job["state"] == "completed", job
        file_id = job["file_id"]
        rows = self.wait(lambda: self.settled_derivatives(file_id))
        metadata = next(row for row in rows if row["kind"] == "metadata")
        if must_succeed:
            assert metadata["state"] == "ready", (name, metadata)
        elif metadata["state"] == "failed":
            assert metadata["failure_reason"] in (
                "resource_limit",
                "invalid_source",
                "no_geometry",
                "unsupported_format",
            ), (name, metadata)
        if metadata["state"] == "ready" and name.endswith(
            (".3mf", ".obj", ".stp", ".step")
        ):
            converted = self.request(f"/api/v1/files/{file_id}/stl")
            assert len(converted) > 84, (name, "empty viewer STL")
        original = self.request(f"/api/v1/files/{file_id}/download")
        assert hashlib.sha256(original).digest() == hashlib.sha256(data).digest()
        signed = self.request(f"/api/v1/files/{file_id}/slicer-url")["url"]
        assert self.request(signed) == data
        assert self.request("/api/v1/jobs/" + job["job_id"])["staging"] is None
        metrics = self.rows(
            "SELECT kind,state,attempts,failure_reason,duration_ms,peak_rss_bytes,"
            f"updated_at,next_attempt_at FROM artifact_derivatives WHERE file_id={file_id}"
        )
        self.report["cases"].append(
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "derivatives": metrics,
                "cleanup": "released",
                "file_id": file_id,
                "resource_sample": self.sample(),
                "job_attempts": self.rows(
                    f"SELECT kind,attempts FROM jobs WHERE subject_key='file/{file_id}'"
                ),
            }
        )
        assert self.sample()["oom_kill"] == self.baseline["oom_kill"]
        print(name, metadata["state"], round(time.monotonic() - started, 2), flush=True)
        return file_id

    def original_only(self, name, data):
        job = self.upload(name, data)
        assert job["state"] == "completed", job
        file_id = job["file_id"]
        assert self.request(f"/api/v1/files/{file_id}/download") == data
        assert self.request(f"/api/v1/files/{file_id}/derivatives") == []
        assert self.request("/api/v1/jobs/" + job["job_id"])["staging"] is None
        self.report["cases"].append(
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "cleanup": "released",
                "capability": "original_download",
            }
        )

    def burst(self):
        probe = (
            Path(__file__).resolve().parents[1] / "tests/fakes/mesh_resource_burst.py"
        )
        docker("cp", str(probe), self.name + ":/tmp/mesh_resource_burst.py")
        script = """import os
os.environ["PYTHONPATH"] = "/tmp"
from app.modules.media.mesh_isolation import supervise, MeshWorkerError
from app.modules.media.worker_bootstrap import command
from app.modules.media.thumbnail_engine import ThumbnailFailureReason
try:
 supervise(command("mesh_resource_burst", [], 128*1024*1024),
           memory_budget=128*1024*1024, timeout_seconds=30)
except MeshWorkerError as error:
 assert error.reason is ThumbnailFailureReason.RESOURCE_LIMIT, error.reason
else:
 raise AssertionError("unbounded allocation succeeded")
"""
        docker("exec", self.name, "/app/.venv/bin/python", "-c", script)
        self.request("/api/v1/health")
        assert self.sample()["oom_kill"] == self.baseline["oom_kill"]
        self.report["allocation_burst"] = "resource_limit"

    def start(self):
        docker("volume", "create", self.volume)
        docker(
            "run",
            "-d",
            "--name",
            self.name,
            "--memory",
            f"{self.memory}g",
            "--memory-swap",
            f"{self.memory}g",
            "--pids-limit",
            "256",
            "-p",
            "127.0.0.1::8000",
            "-v",
            self.volume + ":/data",
            "-e",
            "VAULT_SETUP_MODE=trusted_network",
            "-e",
            "VAULT_SETUP_ALLOWED_HOSTS=127.0.0.1",
            "-e",
            "OPENBLAS_NUM_THREADS=1",
            "-e",
            "VAULT_MAX_RENDER_JOBS=1",
            self.image,
        )
        port = docker("port", self.name, "8000/tcp").rsplit(":", 1)[1]
        self.origin = "http://127.0.0.1:" + port
        self.ready()
        self.csrf = self.request("/api/v1/setup/session", method="POST")["csrf"]
        setup = self.request(
            "/api/v1/setup",
            {
                "username": "resource-owner",
                "password": "ResourceGate123!",
                "storage_backend": "local",
            },
            method="POST",
        )
        self.token = setup["access_token"]
        self.baseline = self.sample()
        assert self.baseline["oom_kill"] == 0
        self.report["baseline"] = self.baseline

    def ready(self):
        until = time.monotonic() + 240
        while time.monotonic() < until:
            try:
                self.request("/api/v1/health")
                return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.5)
        raise AssertionError("production API did not start")

    def run(self, acceptance: Path | None):
        self.start()
        self.case("warm.stl", content.binary_stl(), must_succeed=True)
        self.case("warm.3mf", three_mf(), must_succeed=True)
        self.burst()
        self.case(
            "valid.obj", b"v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3\n", must_succeed=True
        )
        self.case(
            "native.stp",
            (
                Path(__file__).resolve().parents[1]
                / "tests/fixtures/cascadio_material.stp"
            ).read_bytes(),
            must_succeed=self.memory == 4,
        )
        native = (
            Path(__file__).resolve().parents[1] / "tests/fixtures/cascadio_material.stp"
        ).read_bytes()
        self.case(
            "native.step",
            native.replace(
                b"ISO-10303-21;", b"ISO-10303-21;\n/* suffix acceptance */", 1
            ),
            must_succeed=self.memory == 4,
        )
        self.original_only(
            "drawing.dxf",
            b"0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n11\n10\n21\n10\n0\nENDSEC\n0\nEOF\n",
        )
        self.case("malformed.3mf", b"invalid 3mf package")
        refused = self.case("expansion.3mf", expanding_three_mf())
        before = self.rows(
            f"SELECT * FROM artifact_derivatives WHERE file_id={refused}"
        )
        for _ in range(3):
            self.request(
                "/api/v1/admin/work/derivatives/metadata/regenerate",
                {"mode": "missing"},
                method="POST",
            )
        self.case(
            "after-failure.stl", content.binary_stl(offset=(1, 2, 3)), must_succeed=True
        )
        self.report["before_restart"] = self.sample()
        docker("restart", self.name)
        # Docker can allocate a new ephemeral host port when restarting.
        port = docker("port", self.name, "8000/tcp").rsplit(":", 1)[1]
        self.origin = "http://127.0.0.1:" + port
        self.ready()
        self.report["after_restart"] = self.sample()
        self.case(
            "after-restart.stl", content.binary_stl(offset=(2, 3, 4)), must_succeed=True
        )
        after = self.rows(f"SELECT * FROM artifact_derivatives WHERE file_id={refused}")
        assert before == after, "restart/nudges renewed terminal derivatives"
        self.report["terminal_restart"] = "unchanged"
        # Concurrent public uploads exercise admission and the real job engine.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(
                pool.map(
                    lambda n: self.case(
                        f"concurrent-{n}.stl",
                        content.binary_stl(offset=(n + 10, 0, 0)),
                        must_succeed=True,
                    ),
                    range(3),
                )
            )
        initial = self.sample()["rss"]
        for n in range(12):
            if n % 2:
                self.case(
                    f"mixed-{n}.stl",
                    content.binary_stl(offset=(n + 30, 0, 0)),
                    must_succeed=True,
                )
            else:
                self.case(
                    f"mixed-{n}.3mf",
                    three_mf(build=((1, f"1 0 0 0 1 0 0 0 1 {n} 0 0"),)),
                    must_succeed=True,
                )
            self.report["rss_samples"].append(self.sample()["rss"])
        # Current RSS after warm-up, never ru_maxrss's lifetime high-water mark.
        samples = self.report["rss_samples"]
        assert samples[-1] - initial < 64 * MIB, ("parent retention", initial, samples)
        assert sum(samples[-3:]) / 3 - sum(samples[:3]) / 3 < 32 * MIB, samples
        if acceptance is not None:
            files = sorted(
                p for p in acceptance.rglob("*") if p.suffix.lower() == ".3mf"
            )
            assert len(files) == 2, (
                "acceptance directory must contain the two reported 3MFs"
            )
            for path in files:
                self.case(path.name, path.read_bytes(), must_succeed=self.memory == 4)
        # Failed ZIP input is retained until explicit, owner-authorized discard.
        bad_zip = content.zip_bytes({"../unsafe.stl": b"bad"})
        job = self.upload("unsafe.zip", bad_zip, archive=True)
        assert job["state"] == "failed" and job["staging"]["discard_available"]
        self.request(f"/api/v1/jobs/{job['job_id']}/discard-staging", method="POST")
        assert self.request("/api/v1/jobs/" + job["job_id"])["staging"] is None
        self.case(
            "last-healthy.stl", content.binary_stl(offset=(80, 0, 0)), must_succeed=True
        )
        self.wait(lambda: not self.sample()["workers"])
        final = self.sample()
        assert final["oom_kill"] == self.baseline["oom_kill"]
        assert self.rows("SELECT id FROM staging_leases") == []
        assert self.rows("SELECT operation_id FROM capacity_reservations") == []
        self.report["final"] = final
        self.report["passed"] = True

    def close(self):
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.report["container_logs"] = docker("logs", self.name)
        except subprocess.CalledProcessError:
            pass
        self.report_path.write_text(json.dumps(self.report, indent=2) + "\n")
        subprocess.run(
            ["docker", "rm", "-f", self.name], check=False, stdout=subprocess.DEVNULL
        )
        subprocess.run(
            ["docker", "volume", "rm", self.volume],
            check=False,
            stdout=subprocess.DEVNULL,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--memory-gib", type=int, choices=(1, 4), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    gate = Gate(args.image, args.memory_gib, args.report)
    try:
        gate.run(args.acceptance)
    except BaseException as error:
        gate.report["error"] = str(error)
        raise
    finally:
        gate.close()


if __name__ == "__main__":
    main()
