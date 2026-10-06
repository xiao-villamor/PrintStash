"""Autogenerate and render the versioned non-table library database contract."""

from alembic.autogenerate import comparators, renderers
from alembic.operations import ops
from sqlalchemy import inspect


class LibraryContractsOp(ops.MigrateOperation):
    def __init__(self, install: bool = True):
        self.install = install

    def to_diff_tuple(self):
        return ("library_contracts_v1", self.install)

    def reverse(self):
        return LibraryContractsOp(not self.install)


@comparators.dispatch_for("schema")
def compare_library_contracts(context, upgrade_ops, schemas):
    if context.connection is None:
        raise ValueError("library_contract_autogenerate_connection_required")
    if "library_revision" not in inspect(context.connection).get_table_names():
        upgrade_ops.ops.append(LibraryContractsOp())


@renderers.dispatch_for(LibraryContractsOp)
def render_library_contracts(context, operation):
    context.imports.add("from app.db import library_contracts_v1")
    command = "install" if operation.install else "uninstall"
    return f"library_contracts_v1.{command}(op.get_bind())"
