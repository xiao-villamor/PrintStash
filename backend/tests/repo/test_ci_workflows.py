"""CI keeps a short required gate and fails closed before image promotion."""

from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
import tomllib
from fnmatch import fnmatch
from io import BytesIO

import pytest
import yaml

from tests.paths import REPO_ROOT


def _workflow(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / ".github/workflows" / name).read_text())


def _commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def _verifier_module():
    path = REPO_ROOT / "scripts/verify-ci-run.py"
    spec = importlib.util.spec_from_file_location("verify_ci_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _api_run(
    sha: str, status: str, conclusion: str | None, *, workflow: str = "ci.yml"
) -> dict:
    return {
        "head_sha": sha,
        "head_branch": "main",
        "event": "push" if workflow == "ci.yml" else "workflow_dispatch",
        "run_started_at": "2026-09-28T01:00:00Z",
        "created_at": "2026-09-28T01:00:00Z",
        "run_number": 1,
        "run_attempt": 1,
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://github.com/example/PrintStash/actions/runs/1",
    }


class TestQuickGate:
    def test_propagates_required_job_failure(self) -> None:
        jobs = _workflow("ci.yml")["jobs"]
        gate = jobs["gate"]
        assert set(gate["needs"]) == {
            "backend",
            "printer-core",
            "frontend",
            "browser-smoke",
            "browser-extension",
        }
        assert gate["if"] == "${{ always() }}"
        assert 'all(.[]; .result == "success")' in _commands(gate)
        assert all(
            jobs[name].get("continue-on-error") is not True for name in gate["needs"]
        )

    def test_runs_backend_pr_lane_once(self) -> None:
        jobs = _workflow("ci.yml")["jobs"]
        commands = _commands(jobs["backend"])
        assert commands.count("./scripts/test.sh pr ${{ matrix.paths }} -q") == 1
        assert "./scripts/test.sh coverage" not in commands
        static_steps = [
            step
            for step in jobs["backend"]["steps"]
            if "ruff" in step.get("run", "") or "pyright" in step.get("run", "")
        ]
        assert all(step["if"] == "matrix.suite == 'unit'" for step in static_steps)
        runner = (REPO_ROOT / "backend/scripts/test.sh").read_text()
        assert (
            "add_paths tests/unit tests/integration tests/contract tests/e2e tests/repo"
            in runner
        )
        assert "not slow and not coverage_gate and $non_resource_expression" in runner
        assert "export PRINTSTASH_TEST_NO_EXTERNAL=1" in runner

    def test_backend_shards_cover_every_test_file_once(self) -> None:
        rows = _workflow("ci.yml")["jobs"]["backend"]["strategy"]["matrix"]["include"]
        roots = [path for row in rows for path in row["paths"].split()]
        assert len({row["suite"] for row in rows}) == len(rows)
        for test in (REPO_ROOT / "backend/tests").rglob("test_*.py"):
            relative = test.relative_to(REPO_ROOT / "backend")
            if relative.parts[1] not in {
                "unit",
                "integration",
                "contract",
                "e2e",
                "repo",
            }:
                continue
            assert (
                sum(
                    fnmatch(str(relative), path)
                    if "*" in path
                    else relative.is_relative_to(path)
                    for path in roots
                )
                == 1
            ), relative

    def test_pr_selection_excludes_container_backed_e2e(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:randomly",
                "-m",
                "not slow and not coverage_gate and not postgres and not s3 and not remote_storage and not bgcode",
                "tests/e2e/test_job_engine.py",
                "tests/e2e/test_split_topology.py",
                "tests/contract/runtime/test_realtime.py",
                "tests/integration/db/migrations/test_multipart_guides_migration.py",
            ],
            cwd=REPO_ROOT / "backend",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "[sqlite]" in result.stdout
        assert "[postgresql]" not in result.stdout
        assert "test_split_topology.py::" not in result.stdout
        assert "test_realtime.py::" not in result.stdout
        assert "test_postgres_group_delete_detaches_its_guide" not in result.stdout
        assert "test_upgrade_preserves_a_document_before_linking_it" in result.stdout

    def test_runs_two_real_browser_smokes(self) -> None:
        commands = _commands(_workflow("ci.yml")["jobs"]["browser-smoke"])
        package = json.loads((REPO_ROOT / "frontend/package.json").read_text())
        assert "pnpm test:e2e:pr" in commands
        assert package["scripts"]["test:e2e:pr"].endswith("--grep @pr-smoke")
        for spec in ("models.spec.ts", "backup-recovery.spec.ts"):
            source = (REPO_ROOT / "frontend/tests/e2e-real" / spec).read_text()
            assert "@pr-smoke" in source

    def test_keeps_required_check_present_on_every_pr(self) -> None:
        workflow = _workflow("ci.yml")
        assert set(workflow[True]) == {"pull_request", "push", "merge_group"}
        assert workflow[True]["pull_request"] is None
        assert workflow["env"]["OPENBLAS_NUM_THREADS"] == "1"
        assert "gate" in workflow["jobs"]


class TestDeepSuite:
    def test_covers_every_critical_browser_configuration(self) -> None:
        workflow = _workflow("deep-ci.yml")
        browser = workflow["jobs"]["browser-real"]
        commands = _commands(browser)
        manifest = json.loads((REPO_ROOT / "critical-capabilities.json").read_text())
        configs = {
            target["config"]
            for capability in manifest["capabilities"]
            for target in capability["tests"]
            if target["runtime"] == "playwright"
        }
        for config in configs:
            assert f"--config={config}" in commands
        assert "./scripts/test.sh coverage -q" in _commands(workflow["jobs"]["backend"])
        assert all(
            (REPO_ROOT / target["path"]).exists()
            for capability in manifest["capabilities"]
            for target in capability["tests"]
            if target["runtime"] == "pytest"
        )
        assert "./scripts/test-critical.sh" not in str(workflow)

    def test_runs_expensive_work_only_on_schedule_or_dispatch(self) -> None:
        events = _workflow("deep-ci.yml")[True]
        assert set(events) == {"schedule", "workflow_dispatch"}
        assert "flaky-detection" not in _workflow("deep-ci.yml")["jobs"]
        assert "sqlite-async" not in _workflow("deep-ci.yml")["jobs"]

    def test_measures_core_only_once_on_primary_matrix_row(self) -> None:
        steps = _workflow("deep-ci.yml")["jobs"]["printer-core"]["steps"]
        regular = next(
            step for step in steps if step.get("name") == "Test autonomous package"
        )
        coverage = next(
            step
            for step in steps
            if step.get("name") == "Coverage gate for the autonomous package"
        )
        assert (
            regular["if"] == "matrix.python != '3.11' || matrix.resolution != 'highest'"
        )
        assert (
            coverage["if"]
            == "matrix.python == '3.11' && matrix.resolution == 'highest'"
        )


class TestPublicationGuards:
    def test_release_requires_both_results_for_the_tag_commit(self) -> None:
        jobs = _workflow("ghcr.yml")["jobs"]
        guard = _commands(jobs["guard"])
        assert "scripts/verify-release-version.py" in guard
        assert "git merge-base --is-ancestor HEAD origin/main" in guard
        assert 'scripts/verify-ci-run.py ci.yml "$sha"' in guard
        assert 'scripts/verify-ci-run.py deep-ci.yml "$sha"' in guard
        assert jobs["publish"]["needs"] == "guard"
        assert "ci" not in jobs

    def test_nightly_requires_main_ci(self) -> None:
        jobs = _workflow("nightly.yml")["jobs"]
        guard = _commands(jobs["guard"])
        assert '"$REF" != "refs/heads/main"' in guard
        assert 'scripts/verify-ci-run.py ci.yml "$GITHUB_SHA"' in guard
        assert jobs["publish"]["needs"] == "guard"
        assert jobs["publish"]["with"] == {"nightly": True}
        assert "ci" not in jobs

    def test_nightly_images_publish_mutable_tag(self) -> None:
        workflow = _workflow("container-publish.yml")
        metadata = next(
            step
            for step in workflow["jobs"]["merge"]["steps"]
            if step.get("id") == "meta"
        )
        tags = metadata["with"]["tags"]

        assert "type=raw,value=nightly,enable=${{ inputs.nightly }}" in tags
        assert "type=raw,value=canary" not in tags

    def test_nightly_images_publish_commit_tag(self) -> None:
        workflow = _workflow("container-publish.yml")
        metadata = next(
            step
            for step in workflow["jobs"]["merge"]["steps"]
            if step.get("id") == "meta"
        )
        tags = metadata["with"]["tags"]

        assert "type=sha,prefix=nightly-,enable=${{ inputs.nightly }}" in tags

    def test_manual_images_require_main_ci(self) -> None:
        jobs = _workflow("docker-publish.yml")["jobs"]
        guard = _commands(jobs["guard"])
        assert '"$REF" != "refs/heads/main"' in guard
        assert 'scripts/verify-ci-run.py ci.yml "$GITHUB_SHA"' in guard
        assert jobs["publish"]["needs"] == "guard"

    def test_latest_failed_run_cannot_hide_behind_previous_success(self) -> None:
        namespace = runpy.run_path(str(REPO_ROOT / "scripts/verify-ci-run.py"))
        choose = namespace["latest_main_run"]
        sha = "a" * 40
        older = {
            "head_sha": sha,
            "head_branch": "main",
            "event": "push",
            "run_started_at": "2026-09-28T01:00:00Z",
            "created_at": "2026-09-28T01:00:00Z",
            "run_number": 1,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
        }
        newest = {
            **older,
            "run_started_at": "2026-09-28T02:00:00Z",
            "run_number": 2,
            "conclusion": "failure",
        }
        assert (
            choose({"workflow_runs": [older, newest]}, sha=sha, workflow="ci.yml")
            == newest
        )

    @pytest.mark.parametrize(
        ("runs", "reason"),
        [
            pytest.param([], "No {workflow} run", id="missing"),
            pytest.param([("completed", "failure")], "completed/failure", id="failed"),
            pytest.param([("in_progress", None)], "in_progress/None", id="pending"),
        ],
    )
    @pytest.mark.parametrize("workflow", ["ci.yml", "deep-ci.yml"])
    def test_rejects_untrusted_main_result(
        self, monkeypatch, capsys, runs, reason, workflow
    ) -> None:
        module = _verifier_module()
        sha = "a" * 40
        document = {
            "workflow_runs": [
                _api_run(sha, status, conclusion, workflow=workflow)
                for status, conclusion in runs
            ]
        }
        monkeypatch.setattr(
            module,
            "urlopen",
            lambda request, timeout: BytesIO(json.dumps(document).encode()),
        )
        monkeypatch.setenv("GITHUB_REPOSITORY", "example/PrintStash")
        monkeypatch.setenv("GITHUB_TOKEN", "test-only")
        monkeypatch.setattr(sys, "argv", ["verify-ci-run.py", workflow, sha])

        assert module.main() == 1
        assert reason.format(workflow=workflow) in capsys.readouterr().err

    def test_accepts_successful_main_result(self, monkeypatch, capsys) -> None:
        module = _verifier_module()
        sha = "a" * 40
        document = {"workflow_runs": [_api_run(sha, "completed", "success")]}
        monkeypatch.setattr(
            module,
            "urlopen",
            lambda request, timeout: BytesIO(json.dumps(document).encode()),
        )
        monkeypatch.setenv("GITHUB_REPOSITORY", "example/PrintStash")
        monkeypatch.setenv("GITHUB_TOKEN", "test-only")
        monkeypatch.setattr(sys, "argv", ["verify-ci-run.py", "ci.yml", sha])

        assert module.main() == 0
        assert "Verified ci.yml" in capsys.readouterr().out

    def test_does_not_reuse_green_result_from_another_commit(self) -> None:
        choose = _verifier_module().latest_main_run
        wanted = "a" * 40
        other_commit = _api_run("b" * 40, "completed", "success")
        other_branch = {
            **_api_run(wanted, "completed", "success"),
            "head_branch": "feature",
        }

        assert (
            choose(
                {"workflow_runs": [other_commit, other_branch]},
                sha=wanted,
                workflow="ci.yml",
            )
            is None
        )

    def test_release_version_guard_accepts_repository_version(self) -> None:
        script = REPO_ROOT / "scripts/verify-release-version.py"
        version = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())[
            "project"
        ]["version"]
        result = subprocess.run(
            [sys.executable, str(script), f"v{version}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_release_version_guard_rejects_wrong_tag(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/verify-release-version.py"),
                "v99.0.0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "disagrees with" in result.stderr


class TestImageGraph:
    def test_builds_four_targets_on_each_native_architecture(self) -> None:
        workflow = _workflow("container-publish.yml")
        rows = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
        assert {(row["arch"], row["platform"], row["runner"]) for row in rows} == {
            ("amd64", "linux/amd64", "ubuntu-latest"),
            ("arm64", "linux/arm64", "ubuntu-24.04-arm"),
        }
        bake = next(
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step.get("id") == "bake"
        )
        assert bake["with"]["targets"] == "publish"
        for target in ("api", "api-lite", "frontend", "unified"):
            assert f"{target}.output=type=image" in bake["with"]["set"]
        assert "push-by-digest=true" in bake["with"]["set"]

    def test_promotes_only_smoked_digests(self) -> None:
        workflow = _workflow("container-publish.yml")
        steps = workflow["jobs"]["build"]["steps"]
        smoke = next(
            step for step in steps if step.get("name", "").startswith("Smoke every")
        )
        # Bake metadata can exceed Linux's per-argument environment limit.
        # The smoke process needs only four small, validated digests.
        assert "METADATA" not in smoke["env"]
        assert all(
            f"{target}['containerimage.digest']" in smoke["env"][name]
            for name, target in (
                ("API_DIGEST", ".api"),
                ("API_LITE_DIGEST", "['api-lite']"),
                ("FRONTEND_DIGEST", ".frontend"),
                ("UNIFIED_DIGEST", ".unified"),
            )
        )
        assert "^sha256:[0-9a-f]{64}$" in smoke["run"]
        upload = next(
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        )
        assert steps.index(smoke) < steps.index(upload)
        for expected in (
            "./scripts/test.sh image",
            "test-unified-image.sh",
            "frontend_container",
            "--network-alias api",
            "/api/v1/health",
        ):
            assert expected in smoke["run"]
        merge = workflow["jobs"]["merge"]
        assert merge["needs"] == "build"
        assert set(merge["strategy"]["matrix"]["image"]) == {
            "printstash-api",
            "printstash-api-lite",
            "printstash-frontend",
            "printstash",
        }
        assert 'test "${#files[@]}" -eq 1' in _commands(merge)
        assert "docker buildx imagetools create" in _commands(merge)


class TestCoveragePolicy:
    @pytest.mark.parametrize(
        ("percentage", "fails"),
        [(100, False), (89, True)],
        ids=["improvement", "regression"],
    )
    def test_backend_floor(self, monkeypatch, percentage: int, fails: bool) -> None:
        path = REPO_ROOT / "backend/tests/repo/test_coverage_floors.py"
        spec = importlib.util.spec_from_file_location("backend_coverage_floors", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(
            module, "_report", lambda: {"totals": {"percent_covered": percentage}}
        )

        check = module.TestAggregateFloor().test_total_coverage_holds_its_floor
        if fails:
            with pytest.raises(AssertionError, match="below the 90.0% floor"):
                check()
        else:
            check()

    @pytest.mark.parametrize(
        ("percentage", "fails"),
        [(100, False), (98, True)],
        ids=["improvement", "regression"],
    )
    def test_core_floor(self, monkeypatch, percentage: int, fails: bool) -> None:
        path = (
            REPO_ROOT
            / "backend/packages/printstash-core/tests/repo/test_coverage_floors.py"
        )
        spec = importlib.util.spec_from_file_location("core_coverage_floors", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(
            module, "_report", lambda: {"totals": {"percent_covered": percentage}}
        )

        check = module.TestAggregateFloor().test_total_coverage_holds_its_floor
        if fails:
            with pytest.raises(AssertionError, match="below the 99.03% floor"):
                check()
        else:
            check()
