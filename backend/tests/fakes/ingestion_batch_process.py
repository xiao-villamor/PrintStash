"""Real DBOS batch roles with an observable first-commit boundary.

The SQLAlchemy observer holds a transaction that has ALREADY committed. It
never substitutes ingestion, storage, archive reads, or the engine. A parent
can cancel through HTTP or SIGKILL the process while that confirmed Artifact
is visible and before the worker can consume the next archive member.
"""

from __future__ import annotations

import faulthandler
import hashlib
import json
import stat
import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

from app.core.config import ensure_dirs, settings
from app.db.models import IngestionEntry, IngestionEntryState
from app.db.session import get_session_factory
from tests.fakes.job_engine_process import _diagnostics, _emit, _follow, _set_up

_DEADLINE_S = 70


def _entries(job_id: str) -> list[dict]:
    with get_session_factory().scoped_session() as session:
        return [
            dict(row)
            for row in session.execute(
                text(
                    "SELECT id, file_id, state FROM ingestion_entries "
                    "WHERE job_id = :job ORDER BY id"
                ),
                {"job": job_id},
            ).mappings()
        ]


def _staging_snapshot() -> dict:
    """Charge each observed regular inode once, retaining every visited path.

    This is a non-atomic scan: cleanup can rename a file into quarantine after
    its old path was visited. Hardlinks and those rename observations are path
    aliases, not additional allocations. Distinct inodes remain fully charged.
    """
    files = []
    inodes: dict[tuple[int, int], dict[str, int]] = {}
    for path in Path(settings.staging_dir).rglob("*"):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        item = {
            "path": str(path.relative_to(settings.staging_dir)),
            "device": info.st_dev,
            "inode": info.st_ino,
            "size": info.st_size,
            "allocated_bytes": info.st_blocks * 512,
            "links": info.st_nlink,
        }
        files.append(item)
        identity = (info.st_dev, info.st_ino)
        previous = inodes.get(identity)
        # If a growing inode is observed at two names, charge its larger
        # observation rather than depending on traversal order.
        if previous is None:
            inodes[identity] = {
                "size": item["size"],
                "allocated_bytes": item["allocated_bytes"],
            }
        else:
            inodes[identity] = {
                "size": max(previous["size"], item["size"]),
                "allocated_bytes": max(
                    previous["allocated_bytes"], item["allocated_bytes"]
                ),
            }
    return {
        "bytes": sum(item["size"] for item in inodes.values()),
        "logical_bytes": sum(item["size"] for item in files),
        "allocated_bytes": sum(item["allocated_bytes"] for item in inodes.values()),
        "files": files,
    }


class BatchObservation:
    """Observe the sampled staging peak at 2ms intervals and every commit.

    Sampling spans the whole selection; it is not a mathematical supremum
    between samples. Commit boundaries additionally catch each fully expanded
    input while the worker still owns that window.
    """

    def __init__(self) -> None:
        self.job_id: str | None = None
        self.hold_first = True
        self.first = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.stop = threading.Event()
        self.guard = threading.Lock()
        self.sample_guard = threading.Lock()
        self.samples: list[dict] = []
        self.expanded_paths: set[str] = set()
        self.thread = threading.Thread(target=self._sample_loop, daemon=True)

    def sample(self, imported: int = -1) -> None:
        snapshot = _staging_snapshot()
        with self.sample_guard:
            self.samples.append({**snapshot, "imported": imported})

    def _sample_loop(self) -> None:
        while not self.stop.wait(0.002):
            self.sample()

    def before_flush(self, session: Session, _context, _instances) -> None:
        # Only a transaction that itself transitions a receipt to IMPORTED
        # owns this boundary. Unrelated Job/derivative commits may observe the
        # same global row, but must never hold the wrong executor thread.
        for row in session.new.union(session.dirty):
            if not isinstance(row, IngestionEntry):
                continue
            if row.state is not IngestionEntryState.IMPORTED:
                continue
            if (
                row not in session.new
                and not inspect(row).attrs.state.history.has_changes()
            ):
                continue
            assert row.job_id is not None
            session.info["batch_observation_imported_job"] = row.job_id

    def after_rollback(self, session: Session) -> None:
        session.info.pop("batch_observation_imported_job", None)

    def after_commit(self, session: Session) -> None:
        committed_job = session.info.pop("batch_observation_imported_job", None)
        # Selection may start before its HTTP acknowledgement returns. Its
        # own flush marker identifies the Job without depending on that timing.
        if self.job_id is None and committed_job is not None:
            self.job_id = committed_job
        if self.job_id is None:
            self.sample()
            return
        entries = _entries(self.job_id)
        imported = sum(row["state"] == "imported" for row in entries)
        self.sample(imported)
        if committed_job != self.job_id or imported != 1 or not self.hold_first:
            return
        with self.guard:
            if not self.hold_first:
                return
            self.hold_first = False
        self.first.set()
        assert self.release.wait(_DEADLINE_S), "first batch commit was never released"
        self.returned.set()

    def observe_open(self, name: str, arguments: tuple) -> None:
        if name != "open" or not isinstance(arguments[0], (str, bytes)):
            return
        path = Path(
            arguments[0].decode() if isinstance(arguments[0], bytes) else arguments[0]
        )
        if path.suffix == ".gcode" and "scratch-windows" in path.parts:
            self.expanded_paths.add(str(path))

    def begin(self) -> None:
        sys.addaudithook(self.observe_open)
        event.listen(Session, "before_flush", self.before_flush)
        event.listen(Session, "after_rollback", self.after_rollback)
        event.listen(Session, "after_commit", self.after_commit)
        self.sample(0)
        self.thread.start()

    def finish(self) -> dict:
        self.release.set()
        self.stop.set()
        self.thread.join(timeout=5)
        event.remove(Session, "before_flush", self.before_flush)
        event.remove(Session, "after_rollback", self.after_rollback)
        event.remove(Session, "after_commit", self.after_commit)
        self.sample()
        physical_peak = max(self.samples, key=lambda row: row["bytes"])
        logical_peak = max(self.samples, key=lambda row: row["logical_bytes"])
        allocated_peak = max(self.samples, key=lambda row: row["allocated_bytes"])
        return {
            "peak_staging_bytes": physical_peak["bytes"],
            "peak_staging_files": physical_peak["files"],
            "peak_logical_staging_bytes": logical_peak["logical_bytes"],
            "peak_logical_staging_files": logical_peak["files"],
            "peak_allocated_staging_bytes": allocated_peak["allocated_bytes"],
            "peak_allocated_staging_files": allocated_peak["files"],
            "sample_count": len(self.samples),
            "commit_counts": sorted(
                {row["imported"] for row in self.samples if row["imported"] >= 0}
            ),
            "final_staging_bytes": self.samples[-1]["bytes"],
            "expanded_paths": sorted(self.expanded_paths),
        }


def _login(client: TestClient) -> None:
    client.headers["Origin"] = "http://testserver"
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "Password123"},
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def _originals(client: TestClient, job_id: str) -> list[dict]:
    result = []
    for entry in _entries(job_id):
        if entry["state"] != "imported":
            continue
        response = client.get(f"/api/v1/files/{entry['file_id']}/download")
        assert response.status_code == 200, response.text
        result.append(
            {
                "file_id": entry["file_id"],
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
        )
    return result


def start(archive: Path) -> None:
    from app.main import app

    ensure_dirs()
    observation = BatchObservation()
    with TestClient(app) as client:
        _set_up(client)
        inspected = client.post(
            "/api/v1/ingest/archive/inspect",
            files={"file": (archive.name, archive.read_bytes(), "application/zip")},
        )
        assert inspected.status_code == 202, inspected.text
        inspection_id = inspected.json()["job_id"]
        assert _follow(client, inspection_id)["state"] == "completed"
        # The retained archive is a durable input, separate from disposable
        # expanded staging. Its size is explicitly charged in the peak bound.
        baseline = _staging_snapshot()
        import zipfile

        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
        observation.begin()
        # The observer discovers the receipt itself if the worker outruns
        # the HTTP acknowledgement; no sleep orders this boundary.
        selected = client.post(
            f"/api/v1/ingest/archive/{inspection_id}/select", json={"names": names}
        )
        assert selected.status_code == 202, selected.text
        job_id = selected.json()["job_id"]
        assert observation.job_id in {None, job_id}
        observation.job_id = job_id
        try:
            assert observation.first.wait(_DEADLINE_S), _diagnostics()
            originals = _originals(client, job_id)
            assert len(originals) == 1, originals
            _emit(
                job_id=job_id,
                first=originals[0],
                staging_baseline=baseline["bytes"],
                staging_baseline_snapshot=baseline,
            )
            command = sys.stdin.readline().strip()
            assert command in {"cancel", "continue"}, command
            if command == "cancel":
                canceled = client.post(f"/api/v1/jobs/{job_id}/cancel")
                assert canceled.status_code == 200, canceled.text
            observation.release.set()
            assert observation.returned.wait(5), "committed worker did not resume"
            final = _follow(client, job_id)
            result = {
                "job": final,
                "entries": _entries(job_id),
                "originals": _originals(client, job_id),
            }
        finally:
            stats = observation.finish()
    # Lifespan has stopped its real executor before the final snapshot. A late
    # second entry cannot be hidden behind an immediately cancelled Job status.
    result["entries"] = _entries(job_id)
    stats["expanded_paths"] = sorted(observation.expanded_paths)
    # Retain identities even on a green run: the parent consumes stdout,
    # while this generated-fixture report remains beside the source archive.
    archive.with_suffix(".observation.json").write_text(
        json.dumps({"baseline": baseline, **stats}, indent=2)
    )
    _emit(**result, **stats)


def recover(job_id: str) -> None:
    from app.main import app

    ensure_dirs()
    with TestClient(app) as client:
        _login(client)
        final = _follow(client, job_id)
        assert final["state"] == "completed", {"job": final, **_diagnostics()}
        originals = _originals(client, job_id)
        with get_session_factory().scoped_session() as session:
            job = session.execute(
                text("SELECT attempts, resubmits FROM jobs WHERE id = :job"),
                {"job": job_id},
            ).one()
            total_files = session.execute(
                text("SELECT count(*) FROM files")
            ).scalar_one()
        _emit(
            job=final,
            entries=_entries(job_id),
            originals=originals,
            attempts=job.attempts,
            resubmits=job.resubmits,
            total_files=total_files,
        )


if __name__ == "__main__":
    faulthandler.dump_traceback_later(100, exit=True)
    if sys.argv[1] == "start":
        start(Path(sys.argv[2]))
    elif sys.argv[1] == "recover":
        recover(sys.argv[2])
    else:
        raise SystemExit("unknown batch process role")
