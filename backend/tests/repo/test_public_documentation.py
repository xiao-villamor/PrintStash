"""Public docs are deliberate; private work records stay out of Git.

Ignore rules must exclude new reports by default while keeping reviewed public
guides available. Inspect the index too so force-adding a private file fails CI.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.paths import REPO_ROOT


class TestPublicDocumentation:
    @pytest.mark.parametrize(
        "path",
        [
            "docs/deployment.md",
            "docs/release-validation.md",
            "docs/architecture/backend.md",
            "docs/adr/0008-job-engine.md",
        ],
        ids=["operator-guide", "release-checklist", "architecture", "decision"],
    )
    def test_public_guides_are_trackable(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "path",
        [
            "reports/private-notes.md",
            "browser-extension/STORE-VALIDATION.md",
            "docs/internal/notes.md",
            "docs/library-startup-audit.md",
            "docs/frontend-m11-performance.md",
            "docs/frontend-architecture/plan.md",
            "docs/benchmarks/new-run/samples.json",
            "docs/adr/0099-pilot/results.csv",
            "docs/testing/new-feature.md",
            "docs/new-document.md",
            "docs/architecture/new-document.md",
        ],
        ids=[
            "private-notes",
            "extension-validation",
            "internal-notes",
            "audit",
            "performance",
            "plan",
            "benchmark-data",
            "pilot-data",
            "validation",
            "new-document",
            "nested-document",
        ],
    )
    def test_private_documents_are_ignored(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--", path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == path

    def test_private_documents_are_absent_from_index(self) -> None:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--ignored",
                "--exclude-per-directory=.gitignore",
                "--",
                "docs",
                "reports",
                "browser-extension",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout == "", (
            "Private documents are tracked; preserve them under reports/ and "
            "remove them from the index:\n" + result.stdout
        )
