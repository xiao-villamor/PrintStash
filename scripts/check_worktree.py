#!/usr/bin/env python3
"""Verify the chosen task branch before editing or publishing; never switch it."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        # Git errors can contain authenticated remote URLs. Keep those private.
        raise RuntimeError(f"git {args[0]} failed")
    return result.stdout.strip()


def inspect(repo: Path, branch: str, remote: str, fetch: bool) -> dict:
    git(repo, "check-ref-format", "--branch", branch)
    if remote.startswith("-") or "/" in remote:
        raise ValueError("invalid remote name")
    current = git(repo, "branch", "--show-current")
    head = git(repo, "rev-parse", "HEAD")
    worktrees = []
    for block in git(repo, "worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        worktrees.append({"path": fields["worktree"], "branch": fields.get("branch")})
    reasons = []
    if current != branch:
        reasons.append("wrong_branch" if current else "detached_head")
    remotes = git(repo, "remote").splitlines()
    if fetch:
        if remote not in remotes:
            raise ValueError("requested remote is not configured")
        git(repo, "fetch", "--prune", remote)
    ref = f"refs/remotes/{remote}/{branch}"
    refs = git(repo, "for-each-ref", "--format=%(refname)", ref).splitlines()
    ahead = behind = 0
    remote_head = None
    if ref in refs:
        remote_head = git(repo, "rev-parse", ref)
        ahead, behind = map(
            int,
            git(repo, "rev-list", "--left-right", "--count", f"HEAD...{ref}").split(),
        )
        if behind:
            reasons.append("remote_diverged" if ahead else "remote_ahead")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "root": git(repo, "rev-parse", "--show-toplevel"),
        "expected_branch": branch,
        "current_branch": current or None,
        "head": head,
        "remote_head": remote_head,
        "remote_refreshed": fetch,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(git(repo, "status", "--porcelain")),
        "worktrees": worktrees,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--branch", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect(args.repo, args.branch, args.remote, args.fetch)
    except (RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
