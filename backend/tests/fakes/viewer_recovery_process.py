"""Real viewer conversion interruption and a known descendant RSS allocation."""

from __future__ import annotations

import faulthandler
import hashlib
import io
import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

_DEADLINE_S = 70


def allocation_child(amount: int) -> None:
    hold = bytearray(amount)
    for offset in range(0, amount, 4096):
        hold[offset] = 1
    print("allocated", flush=True)
    sys.stdin.readline()
    assert hold[0] == 1


def allocation_tree(amount: int) -> None:
    import psutil

    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.fakes.viewer_recovery_process",
            "allocation-child",
            str(amount),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline() == "allocated\n"
        root_rss = psutil.Process().memory_info().rss
        # Keep the allocation resident across many supervision sampling passes.
        time.sleep(0.5)
        print(
            json.dumps(
                {"root_rss_bytes": root_rss, "descendant_allocation_bytes": amount}
            ),
            flush=True,
        )
    finally:
        child.communicate(input="release\n", timeout=10)
        assert child.returncode == 0


def viewer() -> None:
    import fcntl
    import os

    import psutil
    import trimesh
    from fastapi.testclient import TestClient
    from sqlmodel import select

    from app.core.config import ensure_dirs
    from app.db.models import (
        ArtifactDerivative,
        DerivativeKind,
        DerivativeState,
        Job,
        JobKind,
        JobState,
        OwnedStorageObject,
        StorageObjectState,
    )
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.derivatives.source import subject_key
    from tests.factories.geometry import three_mf
    from tests.fakes.job_engine_process import (
        _diagnostics,
        _emit,
        _follow,
        _set_up,
        _settle,
    )

    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        original = three_mf()
        upload = client.post(
            "/api/v1/ingest/model",
            files={"file": ("interrupted.3mf", original, "model/3mf")},
        )
        assert upload.status_code == 202, upload.text
        uploaded = _follow(client, upload.json()["job_id"])
        assert uploaded["state"] == "completed", uploaded
        file_id = uploaded["file_id"]

        def settled_jobs(kind: JobKind, expected_count: int) -> list[Job]:
            deadline = time.monotonic() + _DEADLINE_S
            while True:
                with get_session_factory().scoped_session() as session:
                    rows = session.exec(
                        select(Job).where(
                            Job.kind == kind,
                            Job.subject_key == subject_key(file_id),
                        )
                    ).all()
                assert len(rows) <= expected_count, _diagnostics()
                assert all(
                    row.state not in {JobState.FAILED, JobState.CANCELLED}
                    for row in rows
                ), _diagnostics()
                if len(rows) == expected_count and all(
                    row.state is JobState.COMPLETED for row in rows
                ):
                    return list(rows)
                assert time.monotonic() < deadline, _diagnostics()
                time.sleep(0.1)

        # READY basics may precede the fingerprint continuation and context
        # release. Wait for this Artifact's whole mesh Job before interception.
        settled = _settle(file_id)
        assert set(settled["states"].values()) == {"ready"}, settled
        settled_jobs(JobKind.DERIVATIVES_MESH, 1)
        stopped = threading.Event()
        observed: dict = {}
        failures: list[BaseException] = []

        def intercept() -> None:
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    for process in psutil.Process().children(recursive=False):
                        try:
                            if "app.modules.media.stl_worker" not in process.cmdline():
                                continue
                            process.send_signal(signal.SIGSTOP)
                            stop_deadline = time.monotonic() + 1
                            while process.status() != psutil.STATUS_STOPPED:
                                assert time.monotonic() < stop_deadline, (
                                    "native worker did not stop"
                                )
                                time.sleep(0.001)
                            environment = process.environ()
                            preparation_ticket = Path(
                                environment["PRINTSTASH_PREPARATION_PERMIT_PATH"]
                            )
                            prepared_workspace = (
                                preparation_ticket.parent
                                / "sources"
                                / preparation_ticket.stem
                            )
                            observed.update(
                                native_pid=process.pid,
                                prepared_workspace=str(prepared_workspace),
                                native_ticket=environment[
                                    "PRINTSTASH_NATIVE_PERMIT_PATH"
                                ],
                                preparation_ticket=environment[
                                    "PRINTSTASH_PREPARATION_PERMIT_PATH"
                                ],
                                native_temporary=environment["TMPDIR"],
                            )
                            stopped.set()
                            return
                        except psutil.NoSuchProcess:
                            raise AssertionError(
                                "viewer worker finished before interception"
                            ) from None
                    time.sleep(0.005)
                raise AssertionError("real viewer worker was never observed")
            except BaseException as exc:
                failures.append(exc)
                stopped.set()

        watcher = threading.Thread(target=intercept, daemon=True)
        watcher.start()
        demand = client.get(f"/api/v1/files/{file_id}/stl")
        assert demand.status_code == 202, demand.text
        assert stopped.wait(timeout=35), "viewer watcher exceeded its deadline"
        watcher.join(timeout=1)
        if failures:
            raise failures[0]
        assert observed
        _emit(stage="held", file_id=file_id, **observed)
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            failed = client.get(f"/api/v1/files/{file_id}/stl")
            if failed.status_code == 422:
                break
            assert failed.status_code == 202, failed.text
            assert time.monotonic() < deadline, _diagnostics()
            time.sleep(0.1)
        assert failed.json()["detail"] == "resource_limit", failed.text
        # The public derivative retry is the product's explicit recovery path.
        with get_session_factory().scoped_session() as session:
            derivative = session.exec(
                select(ArtifactDerivative).where(
                    ArtifactDerivative.file_id == file_id,
                    ArtifactDerivative.kind == DerivativeKind.VIEWER_STL,
                )
            ).one()
            assert derivative.state is DerivativeState.FAILED
            assert derivative.storage_key is None
        settled_jobs(JobKind.DERIVATIVES_VIEWER_STL, 1)
        assert not Path(observed["native_temporary"]).exists(), observed
        assert not psutil.pid_exists(observed["native_pid"])
        # Closing a credit deliberately retains its receipt: unlinking it here
        # would hide a surviving inherited FD. Prove the exact grants are free;
        # the next real retry admission must reclaim their receipts/workspace.
        for name in ("native_ticket", "preparation_ticket"):
            path = Path(observed[name])
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue  # Another real admission already reclaimed this grant.
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        retry = client.post(f"/api/v1/files/{file_id}/derivatives/viewer_stl/retry")
        assert retry.status_code == 202, retry.text
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            preview = client.get(f"/api/v1/files/{file_id}/stl")
            if preview.status_code == 200:
                break
            assert preview.status_code == 202, preview.text
            assert time.monotonic() < deadline, _diagnostics()
            time.sleep(0.1)
        settled_jobs(JobKind.DERIVATIVES_VIEWER_STL, 2)
        for name in (
            "native_ticket",
            "preparation_ticket",
            "native_temporary",
            "prepared_workspace",
        ):
            assert not Path(observed[name]).exists(), observed
        mesh = trimesh.load(io.BytesIO(preview.content), file_type="stl", force="mesh")
        assert isinstance(mesh, trimesh.Trimesh)
        assert len(mesh.faces) == 4 and mesh.is_watertight
        download = client.get(f"/api/v1/files/{file_id}/download")
        assert download.status_code == 200 and download.content == original
        with get_session_factory().scoped_session() as session:
            derivative = session.exec(
                select(ArtifactDerivative).where(
                    ArtifactDerivative.file_id == file_id,
                    ArtifactDerivative.kind == DerivativeKind.VIEWER_STL,
                )
            ).one()
            assert derivative.state is DerivativeState.READY
            proof = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == derivative.storage_key
                )
            ).one()
            assert proof.state is StorageObjectState.COMMITTED
            assert proof.size_bytes == len(preview.content)
            assert proof.sha256 == hashlib.sha256(preview.content).hexdigest()
            jobs = session.exec(
                select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
            ).all()
            assert len(jobs) == 2
        _emit(
            stage="recovered",
            failure_reason=failed.json()["detail"],
            triangle_count=len(mesh.faces),
            watertight=bool(mesh.is_watertight),
            original_preserved=True,
            publication_sha256=proof.sha256,
            viewer_job_count=len(jobs),
            failed_job_settled=True,
            failed_credits_released=True,
        )


if __name__ == "__main__":
    faulthandler.dump_traceback_later(175, exit=True)
    role = sys.argv[1]
    if role == "viewer":
        viewer()
    elif role == "allocation-tree":
        allocation_tree(int(sys.argv[2]))
    elif role == "allocation-child":
        allocation_child(int(sys.argv[2]))
    else:
        raise SystemExit(f"unknown viewer recovery role {role}")
