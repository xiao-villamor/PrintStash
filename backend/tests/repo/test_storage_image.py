"""The release wheel and native image jobs cover every advertised transport.

Development wheels cannot prove which services a custom release wheel compiled.
These build contracts keep both backend image variants on both architectures.
"""

from __future__ import annotations

import re
import tomllib

import yaml

from app.modules.storage.transports.compiled import registered_services
from tests.paths import BACKEND_DIR, REPO_ROOT


def _dockerfile_arg(name: str) -> str:
    dockerfile = (BACKEND_DIR / "Dockerfile").read_text()
    match = re.search(rf"^ARG {name}=(\S+)$", dockerfile, re.M)
    assert match is not None, f"Dockerfile does not declare ARG {name}"
    return match.group(1)


def _pyproject_opendal_pins() -> set[str]:
    project = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text())
    groups = project["project"]["optional-dependencies"]
    return {
        requirement.removeprefix("opendal==")
        for requirements in groups.values()
        for requirement in requirements
        if requirement.startswith("opendal")
    }


class TestOpenDALWheel:
    def test_compiles_exactly_the_registered_services(self) -> None:
        # ``memory`` backs in-process tests of the binding; every other service
        # is one a registered transport needs.
        compiled = set(_dockerfile_arg("OPENDAL_SERVICES").split(","))

        assert compiled == {"services-memory"} | {
            service.cargo_feature for service in registered_services()
        }

    def test_passes_the_service_list_to_the_wheel_build(self) -> None:
        dockerfile = (BACKEND_DIR / "Dockerfile").read_text()

        assert '--features "pyo3/extension-module,abi3,${OPENDAL_SERVICES}"' in dockerfile

    def test_installs_the_python_release_pyproject_pins(self) -> None:
        assert _pyproject_opendal_pins() == {_dockerfile_arg("OPENDAL_PYTHON_VERSION")}

    def test_builds_the_core_release_that_publishes_that_python_release(self) -> None:
        # OpenDAL tags core releases; each publishes one Python distribution.
        # Bump the pair together (bindings/python/Cargo.toml at the tag).
        published = {"0.59.5": "0.47.12"}

        assert (
            published[_dockerfile_arg("OPENDAL_SOURCE_VERSION")]
            == _dockerfile_arg("OPENDAL_PYTHON_VERSION")
        )

    def test_full_image_checks_every_registered_service_compiled(self) -> None:
        dockerfile = (BACKEND_DIR / "Dockerfile").read_text()

        assert "full) .venv/bin/python -m app.modules.storage.transports.compiled ;;" in (
            dockerfile
        )


class TestStorageImage:

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
