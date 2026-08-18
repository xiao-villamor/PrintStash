from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "printstash_core"
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


@pytest.mark.parametrize("source_path", sorted(PACKAGE_ROOT.rglob("*.py")))
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
