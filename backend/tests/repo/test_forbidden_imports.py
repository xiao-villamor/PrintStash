"""Defends ``test_core_package_has_no_forbidden_imports`` behavior for the ``repo`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.paths import BACKEND_ROOT

CORE_ROOT = BACKEND_ROOT / "packages" / "printstash-core"
RUNTIME_ROOT = CORE_ROOT / "src" / "printstash_core"
TESTKIT_ROOT = CORE_ROOT / "src" / "printstash_core_testkit"
FORBIDDEN_ROOTS = {
    "app",
    "fastapi",
    "sqlmodel",
    "sqlalchemy",
    "boto3",
    "stripe",
    "workos",
    "psycopg",
    "asyncpg",
    "aiosqlite",
    "pymysql",
    "mysql",
    "sqlite3",
}
TESTKIT_FORBIDDEN_ROOTS = FORBIDDEN_ROOTS - {"fastapi"}


@pytest.mark.parametrize(
    "source_path",
    sorted(RUNTIME_ROOT.rglob("*.py")),
)
def test_core_package_has_no_forbidden_imports(source_path: Path) -> None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0].lower() for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0].lower())

    assert imported_roots.isdisjoint(FORBIDDEN_ROOTS)


@pytest.mark.parametrize("source_path", sorted(TESTKIT_ROOT.rglob("*.py")))
def test_testkit_has_no_application_or_infrastructure_imports(
    source_path: Path,
) -> None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0].lower() for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0].lower())

    assert imported_roots.isdisjoint(TESTKIT_FORBIDDEN_ROOTS)


def test_runtime_package_has_no_mandatory_dependencies() -> None:
    """The wheel remains importable without any optional integration extras."""
    import tomllib

    metadata = tomllib.loads((CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["dependencies"] == []
