"""Autogenerate and render the versioned non-table library database contract."""

from alembic.autogenerate import comparators, renderers
from alembic.operations import ops
from sqlalchemy import inspect

from app.db import library_authorization_v2


class LibraryContractsOp(ops.MigrateOperation):
    def __init__(self, install: bool = True):
        self.install = install

    def to_diff_tuple(self):
        return ("library_contracts_v1", self.install)

    def reverse(self):
        return LibraryContractsOp(not self.install)


class LibraryAuthorizationOp(ops.MigrateOperation):
    def __init__(self, install: bool = True):
        self.install = install

    def to_diff_tuple(self):
        return ("library_authorization_v2", self.install)

    def reverse(self):
        return LibraryAuthorizationOp(not self.install)


@comparators.dispatch_for("schema")
def compare_library_contracts(context, upgrade_ops, schemas):
    if context.connection is None:
        raise ValueError("library_contract_autogenerate_connection_required")
    if "library_revision" not in inspect(context.connection).get_table_names():
        upgrade_ops.ops.append(LibraryContractsOp())
        upgrade_ops.ops.append(LibraryAuthorizationOp())
    elif not library_authorization_v2.installed(context.connection):
        upgrade_ops.ops.append(LibraryAuthorizationOp())


@renderers.dispatch_for(LibraryContractsOp)
def render_library_contracts(context, operation):
    context.imports.add("from app.db import library_contracts_v1")
    command = "install" if operation.install else "uninstall"
    return f"library_contracts_v1.{command}(op.get_bind())"


@renderers.dispatch_for(LibraryAuthorizationOp)
def render_library_authorization(context, operation):
    context.imports.add("from app.db import library_authorization_v2")
    command = "install" if operation.install else "uninstall"
    return f"library_authorization_v2.{command}(op.get_bind())"
