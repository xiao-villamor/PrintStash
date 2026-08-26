"""Regression tests for ARM container-build CI contracts."""

from __future__ import annotations

import yaml

from tests.paths import REPO_ROOT


def _workflow(name: str) -> dict:
    root = REPO_ROOT
    return yaml.safe_load((root / ".github" / "workflows" / name).read_text())


def _ci_workflow() -> dict:
    return _workflow("ci.yml")


def test_arm_images_build_once_on_native_runners() -> None:
    workflow = _ci_workflow()
    job = workflow["jobs"]["docker-build"]
    rows = job["strategy"]["matrix"]["include"]

    arm_rows = [row for row in rows if row.get("platform") == "linux/arm64"]
    assert {row["image"] for row in arm_rows} == {
        "printstash-api",
        "printstash-api-lite",
        "printstash-frontend",
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


def test_full_arm_image_is_loaded_and_step_smoked_in_its_build_job() -> None:
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


def test_publish_builds_each_platform_on_its_native_runner() -> None:
    workflow = _workflow("container-publish.yml")
    job = workflow["jobs"]["build"]
    rows = job["strategy"]["matrix"]["include"]

    assert len(rows) == 6
    assert {(row["image"], row["platform"], row["runner"]) for row in rows} == {
        ("printstash-api", "linux/amd64", "ubuntu-latest"),
        ("printstash-api", "linux/arm64", "ubuntu-24.04-arm"),
        ("printstash-api-lite", "linux/amd64", "ubuntu-latest"),
        ("printstash-api-lite", "linux/arm64", "ubuntu-24.04-arm"),
        ("printstash-frontend", "linux/amd64", "ubuntu-latest"),
        ("printstash-frontend", "linux/arm64", "ubuntu-24.04-arm"),
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
    }
    merge_step = next(
        step
        for step in merge["steps"]
        if step.get("name") == "Create and verify multi-platform manifest"
    )
    assert "docker buildx imagetools create" in merge_step["run"]
    assert "docker buildx imagetools inspect" in merge_step["run"]


def test_publish_entrypoints_share_the_native_multiarch_workflow() -> None:
    expected = "./.github/workflows/container-publish.yml"
    release = _workflow("ghcr.yml")
    manual = _workflow("docker-publish.yml")

    assert release["jobs"]["publish"]["uses"] == expected
    assert manual["jobs"]["publish"]["uses"] == expected
