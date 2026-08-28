"""Undo the process-wide damage a PostgreSQL `create_all` does to `SQLModel.metadata`.

`app/db/models.py` has a foreign-key cycle: `files.model_id -> models.id` and
`models.thumbnail_file_id -> files.id`. SQLAlchemy cannot render both inline, so
`create_all` breaks the cycle — and how it breaks it depends on the dialect. On
PostgreSQL, which supports `ALTER TABLE ... ADD CONSTRAINT`, it pulls the offending
constraints out of the `CREATE TABLE` and emits them separately. To stop them being
rendered inline it sets `ForeignKeyConstraint._create_rule`, and **that lives on the
shared `MetaData`, not on the engine.**

So it is permanent, and it is not dialect-scoped. Once any `create_all` in the
process has targeted PostgreSQL, every later `create_all` omits those constraints —
including one targeting SQLite, which cannot ALTER and therefore just loses them.
`files` goes from three inline foreign keys to none.

That is how a green suite went red at random. `tests/integration/postgres/` calls
`run_migrations` against a real PostgreSQL, whose fresh-install path is
`create_all`. Any test that afterwards built a SQLite database from the models — in
the same xdist worker, so it depended on how work was distributed that run — got a
schema with no foreign keys on `files` or `models`. `tests/e2e/test_backup.py` then
restored one and `run_migrations` correctly refused to adopt it:

    OrphanSchemaError: unversioned database does not match the current schema;
    refusing to stamp head (different foreign keys for files; different foreign
    keys for models; structural difference add_fk)

`tests/e2e` alone is green; `tests/e2e tests/integration/postgres` failed two runs
in five. Nothing was wrong with either test.

The fixture below restores the metadata after every test here, so the leak cannot
outlive the tests that cause it. It is deliberately blunt — clear the attribute on
every constraint rather than trying to remember which ones were touched — because
being wrong in the other direction just means rendering a foreign key inline, which
is what the models ask for.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlmodel import SQLModel


@pytest.fixture(autouse=True)
def _restore_inline_foreign_key_rendering() -> Iterator[None]:
    yield

    for table in SQLModel.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            constraint._create_rule = None  # noqa: SLF001 - see module docstring
