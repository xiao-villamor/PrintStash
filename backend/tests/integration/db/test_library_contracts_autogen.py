"""Autogeneration adds the Library database contract once per schema."""

import pytest
from alembic.autogenerate.api import AutogenContext
from alembic.migration import MigrationContext
from alembic.operations.ops import UpgradeOps
from sqlalchemy import create_engine, text

from app.db.library_contracts_autogen import compare_library_contracts


class TestCompareLibraryContracts:
    @pytest.mark.parametrize("installed", [False, True], ids=["absent", "installed"])
    def test_generates_only_an_absent_contract(self, installed: bool) -> None:
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                if installed:
                    connection.execute(
                        text("CREATE TABLE library_revision (id INTEGER PRIMARY KEY)")
                    )
                context = AutogenContext(MigrationContext.configure(connection))
                operations = UpgradeOps([])
                compare_library_contracts(context, operations, set())
                assert operations.as_diffs() == (
                    [] if installed else [("library_contracts_v1", True)]
                )
        finally:
            engine.dispose()
