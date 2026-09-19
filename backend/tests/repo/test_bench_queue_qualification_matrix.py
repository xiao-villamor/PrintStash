"""The queue qualification matrix bounds identities and database evidence."""

import sqlite3
import subprocess
import sys

import pytest

from scripts.bench_queue_qualification_matrix import (
    CandidateProfile,
    postgres_database_bytes,
    sqlite_database_bytes,
)
from tests.paths import REPO_ROOT


class TestQueueQualificationMatrix:
    def test_direct_cli_resolves_the_backend_scripts_package(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "backend/scripts/bench_queue_qualification_matrix.py"),
                "--help",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert "Compare PrintStash's durable queue" in result.stdout


    def test_reads_real_sqlite_page_allocation(self, tmp_path):
        database = tmp_path / "candidate.sqlite"
        assert sqlite_database_bytes(database) == 0
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE queue_probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO queue_probe VALUES ('measured')")

        assert sqlite_database_bytes(database) > 0


    def test_rejects_untrusted_postgres_database_name_before_docker(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.bench_queue_qualification_matrix.command",
            lambda _args: pytest.fail("invalid identity reached Docker"),
        )

        with pytest.raises(ValueError, match="database name is invalid"):
            postgres_database_bytes("database-container", "postgres'; DROP DATABASE postgres")


    def test_rejects_unknown_candidate_before_container_execution(self, tmp_path):
        profile = CandidateProfile(
            evidence=tmp_path,
            database="sqlite",
            cpus=2,
            network="isolated",
            image="sha256:candidate",
            password="secret",
            database_container=None,
        )

        with pytest.raises(ValueError, match="unsupported queue candidate"):
            profile.run("unknown", "run", recovery=False)
