"""Regression tests for ARM container-build CI contracts."""

from __future__ import annotations

import json
import subprocess
import tomllib

import pytest
import yaml

from tests.paths import REPO_ROOT


def _workflow(name: str) -> dict:
    root = REPO_ROOT
    return yaml.safe_load((root / ".github" / "workflows" / name).read_text())


def _ci_workflow() -> dict:
    return _workflow("ci.yml")


class TestRustToolchainPin:
    def test_uses_one_patched_compiler_across_native_builds(self) -> None:
        expected = "1.98.1"
        toolchain = tomllib.loads(
            (REPO_ROOT / "backend" / "rust" / "rust-toolchain.toml").read_text()
        )
        queue_toolchain = tomllib.loads(
            (
                REPO_ROOT
                / "backend"
                / "qualification"
                / "queue"
                / "rust-toolchain.toml"
            ).read_text()
        )
        assert toolchain["toolchain"]["channel"] == expected
        assert queue_toolchain["toolchain"]["channel"] == expected

        pinned_paths = (
            REPO_ROOT / ".github" / "workflows" / "ci.yml",
            REPO_ROOT / "backend" / "Dockerfile",
            REPO_ROOT / "backend" / "scripts" / "native-coverage.sh",
            REPO_ROOT / "backend" / "qualification" / "queue" / "Dockerfile",
        )
        for path in pinned_paths:
            source = path.read_text()
            assert expected in source, path
            assert "1.91" not in source, path

    def test_pins_the_current_maturin_build_backend(self) -> None:
        native_project = tomllib.loads(
            (REPO_ROOT / "backend" / "rust" / "pyproject.toml").read_text()
        )
        assert native_project["build-system"]["requires"] == ["maturin==1.15.0"]

        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()
        assert dockerfile.count("maturin==1.15.0") == 2


class TestControlledImportBenchmark:
    def test_runs_without_concurrent_ci_jobs(self) -> None:
        jobs = _ci_workflow()["jobs"]
        ordinary_guard = (
            "github.event_name != 'workflow_dispatch' || "
            "inputs.benchmark_imports != true"
        )
        flaky_guard = (
            "(github.event_name == 'schedule' || "
            "github.event_name == 'workflow_dispatch') && "
            "inputs.benchmark_imports != true"
        )

        ordinary_jobs = set(jobs) - {
            "import-benchmark",
            "flaky-detection",
            "queue-qualification",
        }
        assert ordinary_jobs
        assert all(jobs[name]["if"] == ordinary_guard for name in ordinary_jobs)
        assert jobs["flaky-detection"]["if"] == flaky_guard

    def test_preserves_evidence_on_a_dedicated_opt_in_runner(self):
        job = _ci_workflow()["jobs"]["import-benchmark"]
        assert (
            job["if"]
            == "github.event_name == 'workflow_dispatch' && inputs.benchmark_imports"
        )
        assert job["permissions"] == {"contents": "read"}
        commands = [step.get("run", "") for step in job["steps"]]
        assert sum("scripts/bench_matrix.py" in command for command in commands) == 1
        harness = (REPO_ROOT / "backend" / "scripts" / "bench_matrix.py").read_text()
        assert '"bench_gcode.py"' in harness
        assert '"name": "gcode-parse"' in harness
        artifacts = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        ]
        assert {step["with"]["name"] for step in artifacts} == {
            "import-benchmark-evidence-${{ matrix.database }}-${{ matrix.cpus }}cpu",
            "import-benchmark-release-images-${{ matrix.database }}-${{ matrix.cpus }}cpu",
        }
        assert all(step["with"]["if-no-files-found"] == "error" for step in artifacts)
        evidence = next(
            step
            for step in artifacts
            if step["with"]["name"]
            == "import-benchmark-evidence-${{ matrix.database }}-${{ matrix.cpus }}cpu"
        )
        assert evidence["if"] == "always()"
        assert all(step["if"] == "always()" for step in artifacts)
        assert job["strategy"]["max-parallel"] == 1
        assert job["strategy"]["fail-fast"] is False
        assert job["strategy"]["matrix"] == {
            "database": ["sqlite", "postgres"],
            "cpus": [2, 4],
        }


class TestQueueQualification:
    def test_runs_locked_contracts_with_native_coverage(self):
        job = _ci_workflow()["jobs"]["queue-qualification"]
        commands = "\n".join(step.get("run", "") for step in job["steps"])

        assert "cargo +1.98.1 clippy --locked --all-targets --all-features" in commands
        assert "cargo +1.98.1 audit --ignore RUSTSEC-2023-0071" in commands
        assert "cargo +1.98.1 deny --locked" in commands
        assert "cargo +1.98.1 llvm-cov --locked" in commands
        assert job["services"]["postgres"]["image"].endswith(
            "57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
        )
        artifact = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert artifact["if"] == "always()"
        assert artifact["with"]["if-no-files-found"] == "error"

    def test_requires_explicit_manual_dispatch(self) -> None:
        workflow = _ci_workflow()
        trigger = workflow[True]["workflow_dispatch"]["inputs"]["qualify_queue"]

        assert trigger == {
            "description": "Run the deferred Rust queue qualification contracts",
            "type": "boolean",
            "default": False,
        }
        assert (
            workflow["jobs"]["queue-qualification"]["if"]
            == "github.event_name == 'workflow_dispatch' && inputs.qualify_queue"
        )

    def test_benchmark_preserves_serial_profiles(self):
        workflow = _workflow("queue-qualification-benchmark.yml")
        job = workflow["jobs"]["compare"]

        triggers = workflow[True]
        assert set(triggers) == {"pull_request", "workflow_dispatch"}
        assert triggers["pull_request"]["paths"] == [
            "backend/qualification/queue/**",
            "backend/scripts/bench_queue.py",
            "backend/scripts/bench_queue_qualification.py",
            "backend/scripts/bench_queue_qualification_matrix.py",
        ]
        assert workflow["permissions"] == {"contents": "read"}

        assert job["strategy"]["max-parallel"] == 1
        assert job["strategy"]["fail-fast"] is False
        assert job["strategy"]["matrix"] == {
            "database": ["sqlite", "postgres"],
            "cpus": [2, 4],
        }
        command = next(
            step["run"]
            for step in job["steps"]
            if step.get("name") == "Compare current queue with pinned Rust candidates"
        )
        assert "bench_queue_qualification_matrix.py" in command
        artifact = next(
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert artifact["if"] == "always()"
        assert artifact["with"]["if-no-files-found"] == "error"


class TestFlakyDetectionJob:
    def test_excludes_the_coverage_report_audit(self) -> None:
        job = _ci_workflow()["jobs"]["flaky-detection"]
        command = next(
            step["run"]
            for step in job["steps"]
            if step.get("name") == "Run the suite five times with different orderings"
        )

        assert "--deselect tests/repo/test_coverage_floors.py" in command


class TestBackendTimeouts:
    def test_prevents_unbounded_backend_execution(self) -> None:
        assert _ci_workflow()["jobs"]["backend"]["timeout-minutes"] == 60

        project = tomllib.loads((REPO_ROOT / "backend" / "pyproject.toml").read_text())
        assert project["tool"]["pytest"]["ini_options"]["timeout"] == 300
        assert any(
            dependency.startswith("pytest-timeout>=2.4,")
            for dependency in project["project"]["optional-dependencies"]["dev"]
        )


class TestNativeCoverageJob:
    def test_audits_the_native_dependency_graph(self) -> None:
        steps = _ci_workflow()["jobs"]["native-coverage"]["steps"]
        install = next(
            step["run"]
            for step in steps
            if step.get("name")
            == "Install production compiler and coverage instrumentation"
        )
        assert "cargo +1.98.1 install cargo-audit --version 0.22.2 --locked" in install
        assert (
            "cargo +1.98.1 install cargo-llvm-cov --version 0.9.1 --locked" in install
        )

        audit = next(
            step["run"]
            for step in steps
            if step.get("name") == "Audit the native dependency graph"
        )
        assert audit == "cargo +1.98.1 audit"
        assert "--locked" not in audit

    def test_requires_executed_native_coverage(self):
        job = _ci_workflow()["jobs"]["native-coverage"]
        assert not job.get("continue-on-error", False)
        steps = job["steps"]
        run = next(
            step for step in steps if step.get("run") == "./scripts/native-coverage.sh"
        )
        assert not run.get("continue-on-error", False)
        assert run["working-directory"] == "backend"
        artifact = next(
            step
            for step in steps
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert artifact["if"] == "always()"
        assert artifact["with"]["if-no-files-found"] == "error"
        assert artifact["with"]["path"] == "backend/rust/target/native-coverage-report/"

        script = (REPO_ROOT / "backend" / "scripts" / "native-coverage.sh").read_text()
        assert "--package printstash-gcode-core" in script
        assert "--package printstash-libbgcode" in script
        assert '"/rust/gcode-core/src/"' in script
        assert '"/rust/libbgcode-sys/src/"' in script


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
        assert scan["with"]["image"] == ("${{ matrix.image }}:${{ matrix.arch }}-ci")
        assert scan["with"]["report-name"] == (
            "ci-${{ matrix.image }}-${{ matrix.arch }}"
        )

    def test_publish_scans_each_digest_before_promotion(self) -> None:
        workflow = _workflow("container-publish.yml")
        scan_job = workflow["jobs"]["scan"]
        merge_job = workflow["jobs"]["merge"]
        resolve = next(step for step in scan_job["steps"] if step.get("id") == "target")
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
        assert "ghcr.io/anchore/grype:v0.118.0@sha256:" in scan["env"]["GRYPE_IMAGE"]
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
            step
            for step in action["runs"]["steps"]
            if step["name"] == "Upload SARIF report"
        )

        assert workflow_job["permissions"]["security-events"] == "write"
        assert upload["uses"].startswith("github/codeql-action/upload-sarif@")
        assert upload["uses"] != "github/codeql-action/upload-sarif@v4"
        assert upload["continue-on-error"] is True
        assert upload["with"]["sarif_file"].endswith("/report.sarif")


class TestBackendCoverageJob:
    def test_retains_coverage_after_a_failed_floor(self):
        artifacts = [
            step
            for step in _ci_workflow()["jobs"]["backend"]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        ]
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact["if"] == "always()"
        assert artifact["with"]["name"] == "backend-coverage"
        assert artifact["with"]["if-no-files-found"] == "error"
        assert set(artifact["with"]["path"].splitlines()) == {
            "backend/coverage.json",
            "backend/.coverage-html/",
        }
