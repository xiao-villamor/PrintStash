#!/usr/bin/env python3
"""Require a successful main-branch Actions run for the exact commit to publish."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ALLOWED_EVENTS = {
    "ci.yml": frozenset({"push"}),
    "deep-ci.yml": frozenset({"schedule", "workflow_dispatch"}),
}


def latest_main_run(document: dict, *, sha: str, workflow: str) -> dict | None:
    """Select the newest matching run, including failures and in-progress runs."""
    runs = document["workflow_runs"]
    matches = [
        run
        for run in runs
        if run["head_sha"] == sha
        and run["head_branch"] == "main"
        and run["event"] in ALLOWED_EVENTS[workflow]
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda run: (
            run["run_started_at"] or run["created_at"],
            run["run_number"],
            run["run_attempt"],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", choices=ALLOWED_EVENTS)
    parser.add_argument("sha")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("sha must be a full, lowercase Git commit SHA")
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    query = urlencode({"head_sha": args.sha, "per_page": 100})
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        f"{args.workflow}/runs?{query}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            document = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Cannot verify {args.workflow} for {args.sha}: {exc}", file=sys.stderr)
        return 1

    run = latest_main_run(document, sha=args.sha, workflow=args.workflow)
    if run is None:
        print(
            f"No {args.workflow} run on main exists for {args.sha}. "
            "Wait for CI or dispatch Deep CI on this commit before publishing.",
            file=sys.stderr,
        )
        return 1
    if run["status"] != "completed" or run["conclusion"] != "success":
        print(
            f"{args.workflow} is {run['status']}/{run['conclusion']} for {args.sha}: "
            f"{run['html_url']}",
            file=sys.stderr,
        )
        return 1
    print(f"Verified {args.workflow} for {args.sha}: {run['html_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
