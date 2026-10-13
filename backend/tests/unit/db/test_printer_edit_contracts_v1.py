"""Printer edit contracts reject unsupported engines before emitting DDL."""

import io

import pytest
from alembic.migration import MigrationContext
from sqlalchemy import create_mock_engine

from app.db import printer_edit_contracts_v1 as contract


class TestInstall:
    def test_rejects_unsupported_dialect(self) -> None:
        statements: list[str] = []
        connection = create_mock_engine(
            "mysql://",
            lambda statement, *args, **kwargs: statements.append(str(statement)),
        )
        with pytest.raises(
            ValueError, match="^printer_edit_contract_dialect_unsupported$"
        ):
            contract.install(connection)
        assert statements == []


class TestUninstall:
    def test_rejects_unsupported_dialect(self) -> None:
        statements: list[str] = []
        connection = create_mock_engine(
            "mysql://",
            lambda statement, *args, **kwargs: statements.append(str(statement)),
        )
        with pytest.raises(
            ValueError, match="^printer_edit_contract_dialect_unsupported$"
        ):
            contract.uninstall(connection)
        assert statements == []


class TestOfflineRendering:
    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_renders_contract_installation(self, dialect: str) -> None:
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name=dialect, opts={"as_sql": True, "output_buffer": output}
        )
        contract.install(context.connection)
        rendered = output.getvalue()
        assert "CREATE TRIGGER" in rendered
        assert "ps_printer_edit_v1" in rendered
        assert "edit_version + 1" in rendered

    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_renders_contract_removal(self, dialect: str) -> None:
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name=dialect, opts={"as_sql": True, "output_buffer": output}
        )
        contract.uninstall(context.connection)
        rendered = output.getvalue()
        assert "DROP TRIGGER IF EXISTS ps_printer_edit_v1" in rendered
        if dialect == "postgresql":
            assert "DROP FUNCTION IF EXISTS ps_printer_edit_v1()" in rendered
