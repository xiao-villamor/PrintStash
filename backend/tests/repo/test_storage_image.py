"""The release wheel and native image jobs cover every advertised transport.

Development wheels cannot prove which services a custom release wheel compiled.
These build contracts keep the final-image lifecycle check on both architectures.
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
        workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
        job = next(
            value
            for value in workflow["jobs"].values()
            if "matrix" in value.get("strategy", {})
            and any(
                row.get("image") == "printstash-api"
                for row in value["strategy"]["matrix"].get("include", [])
            )
        )
        images = [
            row
            for row in job["strategy"]["matrix"]["include"]
            if row["image"].startswith("printstash-api")
        ]

        assert {(row["image"], row["arch"]) for row in images} == {
            ("printstash-api", "amd64"),
            ("printstash-api", "arm64"),
            ("printstash-api-lite", "amd64"),
            ("printstash-api-lite", "arm64"),
        }
        assert all(row["load"] and row["storage-smoke"] for row in images)
        assert any("test.sh image" in step.get("run", "") for step in job["steps"])
