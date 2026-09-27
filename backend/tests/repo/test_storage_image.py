"""The release wheel and native image jobs cover every advertised transport.

Development wheels cannot prove which services a custom release wheel compiled.
These build contracts keep both backend image variants on both architectures.
"""

from __future__ import annotations

import re

import yaml

from tests.paths import BACKEND_DIR, REPO_ROOT


class TestStorageImage:
    def test_builds_the_required_s3_service(self) -> None:
        dockerfile = (BACKEND_DIR / "Dockerfile").read_text()

        feature_line = re.search(r"--features\s+([^\s]+)", dockerfile)

        assert feature_line is not None
        assert "services-s3" in feature_line.group(1).split(",")

    def test_checks_each_backend_image_on_its_native_architecture(self) -> None:
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/container-publish.yml").read_text()
        )
        job = workflow["jobs"]["build"]
        images = job["strategy"]["matrix"]["include"]

        assert {row["arch"] for row in images} == {"amd64", "arm64"}
        assert all(
            row["platform"] == f"linux/{row['arch']}"
            and row["runner"]
            == ("ubuntu-latest" if row["arch"] == "amd64" else "ubuntu-24.04-arm")
            for row in images
        )
        steps = job["steps"]
        assert any(
            step.get("uses", "").startswith("docker/bake-action@") for step in steps
        )
        smoke = next(
            step
            for step in steps
            if step.get("name") == "Smoke every final image before exporting digests"
        )
        assert "'api printstash-api full'" in smoke["run"]
        assert "'api-lite printstash-api-lite lite'" in smoke["run"]
        assert smoke["run"].index('docker pull "$reference"') < smoke["run"].index(
            "./scripts/test.sh image"
        )
        assert '--image "$reference" --variant "$variant"' in smoke["run"]
        assert steps.index(smoke) < next(
            index
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert any("test-unified-image.sh" in step.get("run", "") for step in steps)
