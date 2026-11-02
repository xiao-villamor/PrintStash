"""Version 2 of the config edit contract extends version 1 and renders its own trigger."""

import io

import pytest
from alembic.migration import MigrationContext
from sqlalchemy import create_mock_engine

from app.db import config_edit_contracts_v1
from app.db import config_edit_contracts_v2 as contract


class TestInstall:
    def test_rejects_unsupported_dialect(self) -> None:
        statements: list[str] = []
        connection = create_mock_engine(
            "mysql://",
            lambda statement, *args, **kwargs: statements.append(str(statement)),
        )
        with pytest.raises(
            ValueError, match="^config_edit_contract_dialect_unsupported$"
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
            ValueError, match="^config_edit_contract_dialect_unsupported$"
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
        assert "ps_vault_config_edit_v2" in rendered
        assert "vault_edit_version + 1" in rendered

    @pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
    def test_renders_contract_removal(self, dialect: str) -> None:
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name=dialect, opts={"as_sql": True, "output_buffer": output}
        )
        contract.uninstall(context.connection)
        rendered = output.getvalue()
        assert "DROP TRIGGER IF EXISTS ps_vault_config_edit_v2" in rendered
        if dialect == "postgresql":
            assert "DROP FUNCTION IF EXISTS ps_vault_config_edit_v2()" in rendered


class TestFields:
    def test_extends_version_one_with_the_preview_control(self) -> None:
        assert contract.EDIT_FIELDS == (
            *config_edit_contracts_v1.EDIT_FIELDS,
            "derivatives_previews_enabled",
        )
