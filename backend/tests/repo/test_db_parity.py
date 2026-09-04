"""The suite's database enforces what production's does.

A test database that is easier than the real one is worse than no test database: it
turns a class of production failure into a class the suite cannot express. That is
not hypothetical here. `PRAGMA foreign_keys` was off for the whole session — the
teardown wiped tables inside a transaction, where SQLite silently ignores the pragma
that was meant to restore it — and the suite therefore went green on a delete path
that returns 500 on a real installation.

Nothing asserted the pragma, so nothing noticed. This file is that assertion, and it
runs in every lane.

`foreign_keys=ON` specifically, because `app/db/session.py` sets it on every
connection and the schema leans on it: soft-delete columns, the ownership ledger and
the staging leases all encode "this row cannot outlive that one" as a constraint
rather than as code.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlmodel import Session

# Every pragma the app installs per connection that a test could plausibly depend
# on, with the value production runs. `journal_mode` is deliberately absent: an
# in-memory database cannot use WAL, and pinning it here would assert something
# about the test engine rather than about parity.
PRODUCTION_PRAGMAS = {"foreign_keys": 1}


class TestTestDatabase:
    @pytest.mark.parametrize("pragma", sorted(PRODUCTION_PRAGMAS))
    def test_matches_the_pragma_production_runs(
        self, db_session: Session, pragma: str
    ) -> None:
        expected = PRODUCTION_PRAGMAS[pragma]

        actual = db_session.exec(text(f"PRAGMA {pragma}")).one()[0]

        assert actual == expected, (
            f"the test database runs PRAGMA {pragma}={actual}, production runs "
            f"{expected}. A constraint production enforces and the suite does not is "
            "a bug the suite cannot fail on — see tests/conftest.py::_truncate_all."
        )

    def test_a_dangling_foreign_key_is_refused(self, db_session: Session) -> None:
        # The pragma reading 1 is not proof it is being applied: it is per-connection,
        # and `db_session` is not necessarily on the connection that was checked
        # above. This asserts the behaviour instead of the setting.
        from sqlalchemy.exc import IntegrityError

        from app.db.models import Collection

        db_session.add(Collection(name="orphan", slug="orphan", created_by=987654))

        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
