"""Library autogeneration emits reversible calls to the immutable database contract."""

import pytest
from alembic.autogenerate.api import AutogenContext
from alembic.migration import MigrationContext
from alembic.operations.ops import UpgradeOps

from app.db.library_contracts_autogen import (
    LibraryContractsOp,
    compare_library_contracts,
    render_library_contracts,
)


class TestCompareLibraryContracts:
    def test_requires_a_live_connection(self) -> None:
        context = AutogenContext(MigrationContext.configure(dialect_name="sqlite"))
        operations = UpgradeOps([])
        with pytest.raises(
            ValueError, match="^library_contract_autogenerate_connection_required$"
        ):
            compare_library_contracts(context, operations, set())
        assert operations.ops == []


class TestRenderLibraryContracts:
    @pytest.mark.parametrize("install", [True, False], ids=["upgrade", "downgrade"])
    def test_renders_versioned_contract_call(self, install: bool) -> None:
        context = AutogenContext(MigrationContext.configure(dialect_name="sqlite"))
        upgrade = LibraryContractsOp()
        operation = upgrade if install else upgrade.reverse()
        command = "install" if install else "uninstall"
        assert render_library_contracts(context, operation) == (
            f"library_contracts_v1.{command}(op.get_bind())"
        )
        assert context.imports == {"from app.db import library_contracts_v1"}
