"""Drive real preview work and expose an OS-stopped cover worker to the parent."""

import hashlib
import signal
import sys
import threading
import time

import psutil
from fastapi.testclient import TestClient

from tests.fakes.job_engine_process import _emit, _follow, _set_up, _settle


def snapshot(file_id):
    from sqlmodel import select

    from app.db.models import ArtifactDerivative, DerivativeKind, File
    from app.db.session import get_session_factory
    from app.modules.storage.storage_backend.runtime import get_backend

    with get_session_factory().scoped_session() as session:
        file = session.get(File, file_id)
        assert file is not None and file.thumbnail_path is not None
        rows = session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == file_id,
                ArtifactDerivative.kind == DerivativeKind.PREVIEWS,
            )
        ).all()
        return {
            "primary_sha256": hashlib.sha256(
                get_backend().read_bytes(file.thumbnail_path)
            ).hexdigest(),
            "preview_states": [row.state.value for row in rows],
            "published_count": sum(row.storage_key is not None for row in rows),
        }


def hold():
    import trimesh

    from app.core.config import ensure_dirs
    from app.main import app

    stopped = threading.Event()
    observed = {}

    def cover_published() -> bool:
        from sqlmodel import col, select

        from app.db.models import File
        from app.db.session import get_session_factory

        with get_session_factory().scoped_session() as session:
            return (
                session.exec(
                    select(File.id).where(col(File.thumbnail_path).is_not(None))
                ).first()
                is not None
            )

    def intercept():
        # The cover pass renders the views after publishing the cover: stop
        # its worker in that window, with the cover durable and views owed.
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            for child in psutil.Process().children(recursive=True):
                try:
                    # Native workers fork from the template without exec: the
                    # cover pass is the template's child, not a fresh command.
                    if (
                        "app.modules.media.worker_template" in child.parent().cmdline()
                        and cover_published()
                    ):
                        child.send_signal(signal.SIGSTOP)
                        observed["native_pid"] = child.pid
                        stopped.set()
                        return
                except psutil.NoSuchProcess:
                    pass
            time.sleep(0.005)
        stopped.set()

    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        watcher = threading.Thread(target=intercept, daemon=True)
        watcher.start()
        upload = client.post(
            "/api/v1/ingest/model",
            files={
                "file": (
                    "restart.stl",
                    # Dense enough that its views outlast the watcher's poll.
                    trimesh.creation.icosphere(subdivisions=6).export(file_type="stl"),
                    "model/stl",
                )
            },
        )
        assert upload.status_code == 202, upload.text
        result = _follow(client, upload.json()["job_id"])
        assert result["state"] == "completed", result
        assert stopped.wait(90) and observed
        file_id = result["file_id"]
        _emit(file_id=file_id, **observed, **snapshot(file_id))
        while True:
            time.sleep(1)


def converge(file_id):
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app):
        outcome = _settle(file_id)
        _emit(**outcome, **snapshot(file_id))


if __name__ == "__main__":
    if sys.argv[1] == "hold":
        hold()
    else:
        converge(int(sys.argv[2]))
