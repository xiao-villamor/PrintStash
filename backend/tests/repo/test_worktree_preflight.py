"""Branch selection failures are visible before implementation changes Git state."""

import json
import subprocess
import sys

import pytest

from tests.paths import REPO_ROOT


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()

    def run(*args, cwd=repo):
        return subprocess.check_output(
            ["git", "-C", str(cwd), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    run("init", "--initial-branch=task")
    run("config", "user.name", "Fixture")
    run("config", "user.email", "fixture@example.invalid")
    run("commit", "--allow-empty", "-m", "initial")
    remote = tmp_path / "remote.git"
    run("clone", "--bare", str(repo), str(remote))
    run("remote", "add", "origin", str(remote))
    run("fetch", "origin")
    return repo, run, remote


def check(repo, *args):
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/check_worktree.py"),
            "--repo",
            str(repo),
            "--branch",
            "task",
            *args,
        ],
        text=True,
        capture_output=True,
    )
    return result.returncode, json.loads(result.stdout)


class TestWorktreePreflight:
    def test_accepts_the_current_remote_tip(self, git_repo):
        repo, run, _ = git_repo
        code, report = check(repo, "--fetch")
        assert code == 0
        assert report["head"] == report["remote_head"] == run("rev-parse", "HEAD")
        assert report["remote_refreshed"] is True

    def test_preserves_dirty_work(self, git_repo):
        repo, run, _ = git_repo
        (repo / "unfinished").write_text("keep me")
        before = run("status", "--porcelain")
        code, report = check(repo)
        assert code == 0
        assert report["dirty"] is True
        assert run("status", "--porcelain") == before
        assert (repo / "unfinished").read_text() == "keep me"

    def test_reports_the_target_checkout(self, git_repo, tmp_path):
        repo, run, _ = git_repo
        run("switch", "-c", "unrelated")
        checkout = tmp_path / "target"
        run("worktree", "add", str(checkout), "task")
        code, report = check(repo)
        assert code == 1
        assert report["reasons"] == ["wrong_branch"]
        assert {"path": str(checkout), "branch": "refs/heads/task"} in report[
            "worktrees"
        ]
        assert run("branch", "--show-current") == "unrelated"

    def test_rejects_detached_head(self, git_repo):
        repo, run, _ = git_repo
        run("checkout", "--detach")
        code, report = check(repo)
        assert code == 1
        assert "detached_head" in report["reasons"]

    @pytest.mark.parametrize("diverged", [False, True])
    def test_rejects_unintegrated_remote_commits(self, git_repo, tmp_path, diverged):
        repo, run, remote = git_repo
        peer = tmp_path / "peer"
        run("clone", str(remote), str(peer))
        run(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "remote change",
            cwd=peer,
        )
        run("push", "origin", "task", cwd=peer)
        if diverged:
            run("commit", "--allow-empty", "-m", "local change")
        head = run("rev-parse", "HEAD")
        code, report = check(repo, "--fetch")
        assert code == 1
        assert report["reasons"] == ["remote_diverged" if diverged else "remote_ahead"]
        assert report["behind"] == 1
        assert run("rev-parse", "HEAD") == head

    def test_accepts_unpublished_local_commits(self, git_repo):
        repo, run, _ = git_repo
        run("commit", "--allow-empty", "-m", "local change")
        code, report = check(repo)
        assert code == 0
        assert report["ahead"] == 1
        assert report["behind"] == 0

    def test_refuses_to_treat_a_failed_fetch_as_current(self, git_repo):
        repo, run, _ = git_repo
        run("remote", "set-url", "origin", str(repo / "absent.git"))
        code, report = check(repo, "--fetch")
        assert code == 2
        assert report == {"ok": False, "error": "git fetch failed"}
