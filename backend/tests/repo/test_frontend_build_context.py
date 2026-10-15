"""The frontend image builds from sources, never from a checkout's installed packages.

`frontend/Dockerfile` installs dependencies in its own stage, copies the
installed `node_modules` trees into the build stage, and then runs `COPY . .`.
Whatever `node_modules` the build context carries lands on top of the trees the
image installed. `frontend/.dockerignore` matched only the root one, so a
developer checkout sent `packages/ui/node_modules` — the host's pnpm links,
resolved against whatever store the image happens to hold — into every image
build.

The context is computed with docker-py's `.dockerignore` matcher, the rules the
Docker SDK applies when it builds a context, over a checkout-shaped tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docker.utils.build import exclude_paths

from tests.paths import REPO_ROOT

DOCKERIGNORE = REPO_ROOT / "frontend" / ".dockerignore"
WORKSPACE_PACKAGES = sorted(
    path.name
    for path in (REPO_ROOT / "frontend" / "packages").iterdir()
    if path.is_dir()
)

SOURCES = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "src/main.tsx",
    "packages/ui/package.json",
    "packages/ui/src/index.ts",
    "packages/domain/src/format.ts",
)


def _context(root: Path, files: tuple[str, ...]) -> set[str]:
    """The files a build of *root* sends, under the real frontend ignore file."""
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    # Read the way docker-py reads it: stripped lines, minus blanks and comments.
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return {
        path for path in exclude_paths(str(root), patterns) if (root / path).is_file()
    }


class TestFrontendBuildContext:
    def test_sends_the_sources_the_image_builds_from(self, tmp_path: Path) -> None:
        context = _context(tmp_path, SOURCES)

        assert context == set(SOURCES)

    def test_drops_the_root_node_modules(self, tmp_path: Path) -> None:
        context = _context(tmp_path, ("package.json", "node_modules/react/index.js"))

        assert context == {"package.json"}

    @pytest.mark.parametrize("package", WORKSPACE_PACKAGES, ids=str)
    def test_drops_a_workspace_package_node_modules(
        self, tmp_path: Path, package: str
    ) -> None:
        context = _context(
            tmp_path,
            (
                f"packages/{package}/package.json",
                f"packages/{package}/node_modules/clsx/index.js",
            ),
        )

        assert context == {f"packages/{package}/package.json"}
