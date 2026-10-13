"""Local recovery jobs preserve authentic old archives and fail on missing evidence."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tests.paths import REPO_ROOT


class TestHistoricalBackupJobs:
    def test_missing_historical_fixture_fails_before_starting_a_candidate(
        self, tmp_path
    ):
        # CI checkouts can be shallow and have no release tags. Give the job a
        # real, isolated release history instead of depending on this checkout.
        repository = tmp_path / "repository"
        scripts = repository / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "check-backup-compatibility.sh"
        shutil.copyfile(REPO_ROOT / "scripts" / script.name, script)
        subprocess.run(
            ["git", "init", str(repository)], check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Backup test",
                "-c",
                "user.email=backup@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-m",
                "Fixture release",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "tag", "v0.14.0"],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            [
                "bash",
                str(script),
                "unused-image",
                "0.14.1",
                str(tmp_path),
            ],
            env={**os.environ, "PRINTSTASH_BACKUP_PYTHON": sys.executable},
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "Missing historical fixture:" in result.stderr
        assert "demo-backup-0.14.0.tar.gz" in result.stderr

    def test_refuses_replacing_an_existing_version(self, tmp_path):
        fixture = tmp_path / "0.14.0"
        fixture.mkdir()
        preserved = fixture / "evidence"
        preserved.write_bytes(b"original fixture")

        result = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts/create-demo-backup.sh"),
                "0.14.0",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "Historical fixture already exists" in result.stderr
        assert preserved.read_bytes() == b"original fixture"

    def test_creator_uses_the_released_corpus(self):
        script = (REPO_ROOT / "scripts/create-demo-backup.sh").read_text()

        assert 'archive "v$version" testdata' in script
        assert "ghcr.io/xiao-villamor/printstash:$version" in script
        assert script.index('backup_compatibility.py" verify') < script.index("mv -T")
        assert "gh release upload" not in script

    @pytest.mark.parametrize(
        "name",
        ["create-demo-backup.sh", "check-backup-compatibility.sh"],
        ids=["create", "verify"],
    )
    def test_job_has_valid_shell_syntax(self, name):
        result = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "scripts" / name)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
