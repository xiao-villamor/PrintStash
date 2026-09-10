"""An abruptly exited process resumes the same run from its on-disk checkpoint."""

import hashlib
import io
import json
import os
import subprocess
import sys

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    FileType,
    GeometryFingerprint,
    SimilarityCandidate,
    SimilarityRun,
)
from app.db.session import _set_sqlite_pragmas
from app.modules.similarity import configuration, runs
from app.modules.storage.storage_backend.runtime import get_backend
from tests._env import use_local_storage
from tests.factories import build_file, build_model, build_user
from tests.paths import BACKEND_DIR, TESTDATA_DIR

WORKER = """
import os, sys
from pathlib import Path
from tests._env import use_local_storage
from app.db.session import get_session_factory
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.runtime import get_backend
use_local_storage(Path(sys.argv[1]))
worker = SimilarityProcessor(get_session_factory(), get_backend())
for _ in range(int(sys.argv[2])):
    if not worker.work_one():
        break
os._exit(int(sys.argv[3]))
"""


class TestProcessRestart:
    def test_resumes_after_process_exit(self, tmp_path):
        root = use_local_storage(tmp_path / "storage")
        url = f"sqlite:///{tmp_path / 'run.sqlite'}"
        engine = create_engine(url)
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        source = (TESTDATA_DIR / "Calibration Cube.stl").read_bytes()
        # Distinct Artifact bytes of the same real model, with an STL header that
        # changes no geometry. Both must retain their own content identity.
        variants = [source, b"restart variant".ljust(80, b" ") + source[80:]]
        with Session(engine) as session:
            actor = build_user(session, superuser=True)
            configuration.update_settings(
                session, actor, {"enabled": True, "sample_points": 256}
            )
            for content in variants:
                model = build_model(session)
                file = build_file(
                    session,
                    model,
                    file_type=FileType.STL,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                )
                file.path = get_backend().blob_key(
                    model.slug, file.version, file.original_filename
                )
                get_backend().write_stream(io.BytesIO(content), file.path)
                session.add(file)
                session.commit()
            run_id = runs.start(session, actor).id
        environment = os.environ | {
            "VAULT_DB_URL": url,
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        first = subprocess.run(
            [sys.executable, "-c", WORKER, str(root), "1", "17"],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert first.returncode == 17, first.stderr
        with Session(engine) as session:
            run = session.get(SimilarityRun, run_id)
            checkpoint = json.loads(run.checkpoint_json)
            ready = session.exec(
                select(GeometryFingerprint).where(GeometryFingerprint.state == "ready")
            ).all()
            assert checkpoint["file_id"] > 0
            assert json.loads(run.counters_json)["ready"] == 1
            retained = [(row.id, row.source_sha256, row.attempts) for row in ready]
        resumed = subprocess.run(
            [sys.executable, "-c", WORKER, str(root), "60", "0"],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert resumed.returncode == 0, resumed.stderr
        with Session(engine) as session:
            run = session.get(SimilarityRun, run_id)
            assert run.state == "completed", (run.failure_code, run.counters_json)
            assert json.loads(run.counters_json)["ready"] == 2
            assert len(session.exec(select(SimilarityRun)).all()) == 1
            candidate = session.exec(select(SimilarityCandidate)).one()
            assert candidate.evidence_class == "identical_geometry"
            assert candidate.exact_equivalence is True
            for key, digest, attempts in retained:
                row = session.get(GeometryFingerprint, key)
                assert (row.source_sha256, row.attempts) == (digest, attempts)
        engine.dispose()
