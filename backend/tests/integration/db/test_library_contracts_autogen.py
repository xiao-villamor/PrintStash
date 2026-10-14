"""Autogeneration adds the Library database contract once per schema."""

import pytest
from alembic.autogenerate.api import AutogenContext
from alembic.migration import MigrationContext
from alembic.operations.ops import UpgradeOps
from sqlalchemy import create_engine, text

from app.db import library_authorization_v2
from app.db.library_contracts_autogen import compare_library_contracts


class TestCompareLibraryContracts:
    @pytest.mark.parametrize(
        "installed,expected",
        [
            ((), [("library_contracts_v1", True), ("library_authorization_v2", True)]),
            (("v1",), [("library_authorization_v2", True)]),
            (("v1", "v2"), []),
        ],
        ids=["absent", "v1-only", "installed"],
    )
    def test_generates_only_the_absent_contracts(
        self, installed: tuple[str, ...], expected: list
    ) -> None:
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                if "v1" in installed:
                    connection.execute(
                        text("CREATE TABLE library_revision (id INTEGER PRIMARY KEY)")
                    )
                if "v2" in installed:
                    connection.execute(
                        text(
                            f"CREATE TRIGGER {library_authorization_v2.MARKER} "
                            "AFTER DELETE ON library_revision BEGIN SELECT 1; END"
                        )
                    )
                context = AutogenContext(MigrationContext.configure(connection))
                operations = UpgradeOps([])
                compare_library_contracts(context, operations, set())
                assert operations.as_diffs() == expected
        finally:
            engine.dispose()
