"""Regression tests for ARM container-build CI contracts."""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from tests.paths import REPO_ROOT


def _workflow(name: str) -> dict:
    root = REPO_ROOT
    return yaml.safe_load((root / ".github" / "workflows" / name).read_text())


def _ci_workflow() -> dict:
    return _workflow("ci.yml")


class TestCriticalCapabilitiesJob:
    """Critical behavior has one visible, independently rerunnable CI signal."""

    def test_job_runs_the_repository_critical_lane(self) -> None:
        job = _ci_workflow()["jobs"]["critical-capabilities"]
        commands = [step.get("run") for step in job["steps"]]

        assert job.get("continue-on-error", False) is False
        assert "./scripts/test-critical.sh" in commands
        assert any(
            command == "uv sync --extra dev --extra full --frozen"
            for command in commands
        )
        assert any(
            command == "pnpm exec playwright install --with-deps chromium"
            for command in commands
        )


class TestBackendRuntimeCompatibilityJob:
    def test_python_314_runtime_is_a_required_ci_gate(self) -> None:
        job = _ci_workflow()["jobs"]["backend-python314"]
        python_setup = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/setup-python@")
        )

        assert job["name"] == "Backend runtime (Python 3.14)"
        assert job.get("continue-on-error", False) is False
        assert python_setup["with"]["python-version"] == "3.14"
        assert any(
            step.get("run") == "uv sync --extra dev --extra full --frozen"
            for step in job["steps"]
        )
        assert any(
            step.get("run") == "./scripts/test.sh full -q" for step in job["steps"]
        )


class TestMultiArchWorkflows:
    """The release workflows build each architecture where it is native.

    ARM images built under emulation take hours and time out; built on native
    runners they take minutes. Nothing in CI notices a regression here except the
    clock, and by then a release is already stuck — so the workflow YAML is
    asserted directly."""

    def test_arm_images_build_once_on_native_runners(self) -> None:
        workflow = _ci_workflow()
        job = workflow["jobs"]["docker-build"]
        rows = job["strategy"]["matrix"]["include"]

        arm_rows = [row for row in rows if row.get("platform") == "linux/arm64"]
        assert {row["image"] for row in arm_rows} == {
            "printstash-api",
            "printstash-api-lite",
            "printstash-frontend",
            "printstash",
        }
        assert all(row["runner"] == "ubuntu-24.04-arm" for row in arm_rows)
        assert job["runs-on"] == "${{ matrix.runner }}"

        build_steps = [
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        ]
        assert len(build_steps) == 1
        assert build_steps[0]["with"]["platforms"] == "${{ matrix.platform }}"

    def test_the_arm_image_is_smoke_tested_in_its_own_build_job(self) -> None:
        workflow = _ci_workflow()
        jobs = workflow["jobs"]
        job = jobs["docker-build"]
        rows = job["strategy"]["matrix"]["include"]

        full_arm_rows = [
            row
            for row in rows
            if row["image"] == "printstash-api" and row.get("platform") == "linux/arm64"
        ]
        assert len(full_arm_rows) == 1
        assert full_arm_rows[0]["load"] is True
        assert full_arm_rows[0]["step-smoke"] is True
        assert "arm-step-runtime" not in jobs

        smoke_steps = [
            step
            for step in job["steps"]
            if step.get("name") == "Tessellate a valid STEP fixture in ARM64 userspace"
        ]
        assert len(smoke_steps) == 1
        assert smoke_steps[0]["if"] == "matrix.step-smoke"

    def test_publish_builds_each_platform_on_its_native_runner(self) -> None:
        workflow = _workflow("container-publish.yml")
        job = workflow["jobs"]["build"]
        rows = job["strategy"]["matrix"]["include"]

        assert len(rows) == 8
        assert {(row["image"], row["platform"], row["runner"]) for row in rows} == {
            ("printstash-api", "linux/amd64", "ubuntu-latest"),
            ("printstash-api", "linux/arm64", "ubuntu-24.04-arm"),
            ("printstash-api-lite", "linux/amd64", "ubuntu-latest"),
            ("printstash-api-lite", "linux/arm64", "ubuntu-24.04-arm"),
            ("printstash-frontend", "linux/amd64", "ubuntu-latest"),
            ("printstash-frontend", "linux/arm64", "ubuntu-24.04-arm"),
            ("printstash", "linux/amd64", "ubuntu-latest"),
            ("printstash", "linux/arm64", "ubuntu-24.04-arm"),
        }
        assert job["runs-on"] == "${{ matrix.runner }}"
        assert all(
            not str(step.get("uses", "")).startswith("docker/setup-qemu-action@")
            for step in job["steps"]
        )
        build_step = next(
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        )
        assert build_step["with"]["platforms"] == "${{ matrix.platform }}"
        assert "push-by-digest=true" in build_step["with"]["outputs"]
        assert build_step["with"]["cache-from"].endswith(
            "${{ matrix.image }}-${{ matrix.arch }}"
        )

        merge = workflow["jobs"]["merge"]
        assert "build" in merge["needs"]
        assert set(merge["strategy"]["matrix"]["image"]) == {
            "printstash-api",
            "printstash-api-lite",
            "printstash-frontend",
            "printstash",
        }
        merge_step = next(
            step
            for step in merge["steps"]
            if step.get("name") == "Create and verify multi-platform manifest"
        )
        assert "docker buildx imagetools create" in merge_step["run"]
        assert "docker buildx imagetools inspect" in merge_step["run"]

    def test_publish_digest_artifacts_use_an_exact_image_delimiter(self) -> None:
        workflow = _workflow("container-publish.yml")
        build_steps = workflow["jobs"]["build"]["steps"]
        merge_steps = workflow["jobs"]["merge"]["steps"]

        upload_step = next(
            step
            for step in build_steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        )
        download_step = next(
            step
            for step in merge_steps
            if str(step.get("uses", "")).startswith("actions/download-artifact@")
        )

        assert upload_step["with"]["name"] == (
            "digests-${{ matrix.image }}--${{ matrix.arch }}"
        )
        assert download_step["with"]["pattern"] == "digests-${{ matrix.image }}--*"

    def test_publish_entrypoints_share_the_native_multiarch_workflow(self) -> None:
        expected = "./.github/workflows/container-publish.yml"
        release = _workflow("ghcr.yml")
        manual = _workflow("docker-publish.yml")

        assert release["jobs"]["publish"]["uses"] == expected
        assert manual["jobs"]["publish"]["uses"] == expected


class TestUnifiedImageWorkflow:
    @pytest.mark.parametrize(
        ("workflow", "job_name"),
        [("ci.yml", "docker-build"), ("container-publish.yml", "build")],
        ids=["pull-request", "publish"],
    )
    def test_bake_uses_authenticated_actions_cache(self, workflow, job_name) -> None:
        steps = _workflow(workflow)["jobs"][job_name]["steps"]
        build = next(
            step
            for step in steps
            if step.get("uses", "").startswith("docker/bake-action@")
        )

        assert build["if"] == "matrix.image == 'printstash'"
        assert build["with"]["source"] == "."
        assert build["with"]["files"] == "docker-bake.hcl"
        assert build["with"]["targets"] == "unified"
        assert "*.platform=${{ matrix.platform }}" in build["with"]["set"]
        assert "type=gha" in build["with"]["set"]

    def test_requires_smoke_test_before_exporting_digest(self) -> None:
        steps = _workflow("container-publish.yml")["jobs"]["build"]["steps"]
        unified = next(step for step in steps if step.get("id") == "unified")
        build = next(step for step in steps if step.get("id") == "unified-build")

        assert unified["if"] == "matrix.image == 'printstash'"
        assert "unified.tags=\n" in build["with"]["set"]
        assert unified["run"].index("test-unified-image.sh") < unified["run"].index(
            'echo "digest='
        )
        assert (
            next(step for step in steps if step["name"] == "Export digest")["env"][
                "DIGEST"
            ]
            == "${{ steps.build.outputs.digest || steps.unified.outputs.digest }}"
        )

    def test_ci_exercises_unified_container(self) -> None:
        steps = _ci_workflow()["jobs"]["docker-build"]["steps"]
        smoke = next(
            step for step in steps if step["name"] == "Smoke-test unified container"
        )

        assert smoke["if"] == "matrix.image == 'printstash'"
        assert "test-unified-image.sh" in smoke["run"]


class TestContainerVulnerabilityScanning:
    def test_ci_scans_every_local_architecture(self) -> None:
        job = _ci_workflow()["jobs"]["docker-build"]
        rows = job["strategy"]["matrix"]["include"]
        scan = next(
            step
            for step in job["steps"]
            if step.get("uses") == "./.github/actions/grype-scan"
        )

        assert len(rows) == 8
        assert all(row.get("load", True) is True for row in rows)
        assert scan["with"]["image"] == (
            "${{ matrix.image }}:${{ matrix.arch }}-ci"
        )
        assert scan["with"]["report-name"] == (
            "ci-${{ matrix.image }}-${{ matrix.arch }}"
        )

    def test_publish_scans_each_digest_before_promotion(self) -> None:
        workflow = _workflow("container-publish.yml")
        scan_job = workflow["jobs"]["scan"]
        merge_job = workflow["jobs"]["merge"]
        resolve = next(
            step for step in scan_job["steps"] if step.get("id") == "target"
        )
        scan = next(
            step
            for step in scan_job["steps"]
            if step.get("uses") == "./.github/actions/grype-scan"
        )

        assert scan_job["needs"] == "build"
        assert len(scan_job["strategy"]["matrix"]["include"]) == 8
        assert "@sha256:${digest}" in resolve["run"]
        assert scan["with"]["image"] == "${{ steps.target.outputs.image }}"
        assert set(merge_job["needs"]) == {"build", "scan"}

    def test_reports_include_each_supported_format(self) -> None:
        action = yaml.safe_load(
            (REPO_ROOT / ".github/actions/grype-scan/action.yml").read_text()
        )
        steps = action["runs"]["steps"]
        scan = next(step for step in steps if step["name"] == "Scan image")
        upload = next(
            step
            for step in steps
            if step["name"] == "Upload vulnerability report bundle"
        )

        assert "--output table=/reports/report.txt" in scan["run"]
        assert "--output json=/reports/report.json" in scan["run"]
        assert "--output sarif=/reports/report.sarif" in scan["run"]
        assert "ghcr.io/anchore/grype:v0.118.0@sha256:" in scan["env"][
            "GRYPE_IMAGE"
        ]
        assert upload["with"]["retention-days"] == 90
        assert upload["with"]["if-no-files-found"] == "warn"

    def test_summary_reports_severity_totals(self) -> None:
        summary_program = REPO_ROOT / ".github/actions/grype-scan/summary.jq"
        report = {
            "matches": [
                {
                    "vulnerability": {
                        "severity": "Critical",
                        "fix": {"versions": ["2.0"]},
                    }
                },
                {
                    "vulnerability": {
                        "severity": "High",
                        "fix": {"versions": []},
                    }
                },
            ]
        }

        result = subprocess.run(
            [
                "jq",
                "-r",
                "--arg",
                "image",
                "printstash-api:amd64-ci",
                "-f",
                str(summary_program),
            ],
            input=json.dumps(report),
            check=True,
            capture_output=True,
            text=True,
        )

        assert "**Image:** `printstash-api:amd64-ci`" in result.stdout
        assert "**Matches:** 2 (1 with a known fix)" in result.stdout
        assert "| Critical | 1 |" in result.stdout
        assert "| High | 1 |" in result.stdout

    def test_vulnerability_matches_remain_report_only(self) -> None:
        action = yaml.safe_load(
            (REPO_ROOT / ".github/actions/grype-scan/action.yml").read_text()
        )
        scan = next(
            step for step in action["runs"]["steps"] if step["name"] == "Scan image"
        )

        assert "--fail-on" not in scan["run"]
        assert scan.get("continue-on-error", False) is False

    def test_trusted_runs_upload_sarif(self) -> None:
        workflow_job = _ci_workflow()["jobs"]["docker-build"]
        action = yaml.safe_load(
            (REPO_ROOT / ".github/actions/grype-scan/action.yml").read_text()
        )
        upload = next(
            step for step in action["runs"]["steps"] if step["name"] == "Upload SARIF report"
        )

        assert workflow_job["permissions"]["security-events"] == "write"
        assert upload["uses"].startswith("github/codeql-action/upload-sarif@")
        assert upload["uses"] != "github/codeql-action/upload-sarif@v4"
        assert upload["continue-on-error"] is True
        assert upload["with"]["sarif_file"].endswith("/report.sarif")
