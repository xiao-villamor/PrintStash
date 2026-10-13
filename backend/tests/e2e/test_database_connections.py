"""E2E connections must match production before concurrent work starts.

A late switch from DELETE journals to WAL can fail while another connection
holds a read transaction, before the HTTP request reaches its handler.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session


class TestE2EDatabaseConnections:
    def test_starts_with_wal_journaling(self, e2e_db: Session):
        engine = e2e_db.get_bind()

        with engine.connect() as connection:
            journal = connection.execute(text("PRAGMA journal_mode")).scalar_one()

        assert journal == "wal"

    def test_commits_a_new_connection_during_a_reader(self, e2e_db: Session):
        engine = e2e_db.get_bind()
        with engine.begin() as setup:
            setup.execute(
                text("CREATE TABLE concurrency_probe (value INTEGER NOT NULL)")
            )
            setup.execute(text("INSERT INTO concurrency_probe VALUES (1)"))

        with engine.connect() as reader:
            reader.execute(text("BEGIN"))
            assert (
                reader.execute(text("SELECT value FROM concurrency_probe")).scalar_one()
                == 1
            )
            with engine.begin() as writer:
                writer.execute(text("UPDATE concurrency_probe SET value = 2"))
            assert (
                reader.execute(text("SELECT value FROM concurrency_probe")).scalar_one()
                == 1
            )

        with engine.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT value FROM concurrency_probe")
                ).scalar_one()
                == 2
            )

    def test_refuses_orphaned_foreign_keys(self, e2e_db: Session):
        engine = e2e_db.get_bind()
        with engine.begin() as setup:
            setup.execute(text("CREATE TABLE parent_probe (id INTEGER PRIMARY KEY)"))
            setup.execute(
                text(
                    "CREATE TABLE child_probe (parent_id INTEGER REFERENCES parent_probe(id))"
                )
            )

        with engine.begin() as writer:
            with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
                writer.execute(text("INSERT INTO child_probe VALUES (7)"))

        with engine.connect() as observer:
            assert (
                observer.execute(text("SELECT count(*) FROM child_probe")).scalar_one()
                == 0
            )
