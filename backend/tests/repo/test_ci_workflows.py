"""Regression tests for CI and release workflow contracts."""

from __future__ import annotations

import yaml

from tests.paths import REPO_ROOT


def _workflow(name: str) -> dict:
    root = REPO_ROOT
    return yaml.safe_load((root / ".github" / "workflows" / name).read_text())


def _ci_workflow() -> dict:
    return _workflow("ci.yml")


class TestCriticalCapabilitiesJob:
    """Critical behavior has one visible, independently rerunnable CI signal."""

    def test_retains_browser_failure_evidence(self) -> None:
        steps = _ci_workflow()["jobs"]["critical-capabilities"]["steps"]
        uploads = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]

        assert len(uploads) == 1
        upload = uploads[0]
        assert upload["if"] == "${{ !cancelled() }}"
        assert set(upload["with"]["path"].splitlines()) == {
            "frontend/playwright-report/",
            "frontend/test-results/",
        }
        assert upload["with"]["name"] == "playwright-report-critical"
        assert upload["with"]["retention-days"] == 7

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


class TestBackendCompatibilityJob:
    def test_full_lane_has_time_for_serial_provider_contracts(self) -> None:
        job = _ci_workflow()["jobs"]["backend-python313"]
        commands = [step.get("run") for step in job["steps"]]

        assert "./scripts/test.sh full -q" in commands
        assert 60 <= job["timeout-minutes"] <= 90


class TestMultiArchWorkflows:
    """Release images build on native runners before multi-platform promotion."""

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
        assert merge["needs"] == "build"
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
    def test_bake_uses_authenticated_actions_cache(self) -> None:
        steps = _workflow("container-publish.yml")["jobs"]["build"]["steps"]
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


class TestLeanCiWorkflows:
    def test_ci_omits_per_image_builds(self) -> None:
        jobs = _ci_workflow()["jobs"]

        assert "docker-build" not in jobs

    def test_ci_omits_legacy_migration(self) -> None:
        jobs = _ci_workflow()["jobs"]

        assert "minio-migration" not in jobs

    def test_publish_promotes_built_digests(self) -> None:
        workflow = _workflow("container-publish.yml")

        assert workflow["jobs"]["merge"]["needs"] == "build"

    def test_publish_omits_scan_jobs(self) -> None:
        workflow = _workflow("container-publish.yml")

        assert "scan" not in workflow["jobs"]
        assert all(
            step.get("uses") != "./.github/actions/grype-scan"
            for job in workflow["jobs"].values()
            for step in job["steps"]
        )
